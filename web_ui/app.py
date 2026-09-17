#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FastAPI backend for the ToT4ES web UI.

Endpoints:
    GET /api/search?q=<name>              -> candidate DBpedia resources
    GET /api/entity?name=<name>&limit=30  -> description + triples for a name
    GET /api/entity?uri=<uri>&limit=30    -> description + triples for a URI
"""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dbpedia_client import (
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_TRIPLE_LIMIT,
    DBpediaError,
    describe_entity,
    get_description,
    get_triples,
    parse_nt_entity,
    search_entities,
)
from llm_api import LLMAPIError, describe_providers
from tot4es_service import summarize_entity

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="ToT4ES Web UI", version="0.1.0")


class EntitySummaryRequest(BaseModel):
    entity_label: str = Field(..., min_length=1, max_length=300)
    triples: List[Dict[str, Any]] = Field(..., min_length=1, max_length=200)
    summary_length: int = Field(5, ge=1, le=20)
    provider: Optional[Literal["dice", "ollama"]] = None
    model: Optional[str] = Field(None, min_length=1, max_length=100)


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(DEFAULT_SEARCH_LIMIT, ge=1, le=50),
):
    """Resolve a typed entity name to candidate DBpedia resources."""
    try:
        return {"query": q, "candidates": search_entities(q, limit)}
    except DBpediaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/entity")
def api_entity(
    name: Optional[str] = Query(None, max_length=200),
    uri: Optional[str] = Query(None, max_length=500),
    limit: int = Query(DEFAULT_TRIPLE_LIMIT, ge=1, le=200),
):
    """Return the description and up to `limit` triples of an entity."""
    if not name and not uri:
        raise HTTPException(status_code=400, detail="Provide either 'name' or 'uri'.")
    try:
        if uri:
            return {
                "query": uri,
                "resolved": get_description(uri),
                "candidates": [],
                "triples": get_triples(uri, limit),
            }
        result = describe_entity(name, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DBpediaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if result["resolved"] is None:
        raise HTTPException(status_code=404, detail=f"No DBpedia entity found for {name!r}.")
    return result


@app.post("/api/entity/upload")
async def api_entity_upload(
    file: UploadFile = File(...),
    limit: int = Query(DEFAULT_TRIPLE_LIMIT, ge=1, le=200),
):
    """Parse one uploaded .nt file containing an entity description."""
    filename = file.filename or ""
    if not filename.lower().endswith(".nt"):
        raise HTTPException(status_code=400, detail="Upload an N-Triples file with a .nt extension.")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The .nt upload must be smaller than 10 MB.")
    try:
        return parse_nt_entity(content, limit)
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="The .nt file must be UTF-8 encoded.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/providers")
def api_providers():
    """List the LLM backends the UI can pick from and their readiness."""
    return {"providers": describe_providers()}


@app.post("/api/summarize")
def api_summarize(request: EntitySummaryRequest):
    """Select an entity summary with the task-decomposed ToT4ES algorithm."""
    try:
        return summarize_entity(
            entity_label=request.entity_label,
            triples=request.triples,
            summary_length=request.summary_length,
            provider=request.provider,
            model=request.model,
        )
    except LLMAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid triple payload: {exc}") from exc


@app.post("/api/summarize/stream")
def api_summarize_stream(request: EntitySummaryRequest):
    """Stream real BFS/LLM search events as newline-delimited JSON."""
    events: queue.Queue = queue.Queue()
    finished = object()

    def emit(event: Dict[str, Any]) -> None:
        events.put(event)

    def run_search() -> None:
        try:
            result = summarize_entity(
                entity_label=request.entity_label,
                triples=request.triples,
                summary_length=request.summary_length,
                provider=request.provider,
                model=request.model,
                event_callback=emit,
            )
            events.put({"type": "result", "result": result})
        except Exception as exc:  # delivered to the browser as a final event
            events.put({"type": "error", "message": str(exc)})
        finally:
            events.put(finished)

    threading.Thread(target=run_search, daemon=True).start()

    def stream():
        while True:
            event = events.get()
            if event is finished:
                break
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
