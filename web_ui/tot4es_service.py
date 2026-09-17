#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Run the repository's task-decomposed ToT4ES search for web requests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from tot_modules import (  # noqa: E402
    TaskDecomposedToT,
    decode_state_to_triples,
    entity_heuristic_calculator,
    make_combined_evaluation_prompt,
    make_diversity_prompt,
    make_informativeness_prompt,
    make_relatedness_prompt,
)

from llm_api import create_chat_client  # noqa: E402

DEFAULT_SUMMARY_LENGTH = 5


def _predicate_frequencies(triples: List[str]) -> Dict[str, int]:
    frequencies: Dict[str, int] = {}
    for triple in triples:
        parts = triple.split(maxsplit=2)
        if len(parts) >= 2:
            frequencies[parts[1]] = frequencies.get(parts[1], 0) + 1
    return frequencies


def _semantic_roles(triples: List[str]) -> Dict[str, str]:
    role_patterns = {
        "location": ("place", "location", "hometown"),
        "time": ("date", "born", "died", "founded", "year"),
        "relationship": ("spouse", "parent", "child", "sibling", "related"),
        "work": ("work", "author", "created", "directed", "produced", "film"),
        "organization": ("member", "company", "institution", "team", "group"),
        "attribute": ("name", "label", "title", "type", "class"),
    }
    roles: Dict[str, str] = {}
    for triple in triples:
        parts = triple.split(maxsplit=2)
        if len(parts) < 2:
            continue
        predicate = parts[1]
        name = predicate.rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower()
        roles[predicate] = next(
            (role for role, patterns in role_patterns.items() if any(pattern in name for pattern in patterns)),
            "other",
        )
    return roles


def _triple_text(triple: Dict[str, Any]) -> str:
    subject = f"<{triple['subject']}>"
    predicate = f"<{triple['predicate']}>"
    if triple["object_is_uri"]:
        object_value = f"<{triple['object']}>"
    else:
        value = str(triple["object"]).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')
        object_value = f'"{value}"'
    return f"{subject} {predicate} {object_value} ."


def summarize_entity(
    entity_label: str,
    triples: List[Dict[str, Any]],
    summary_length: int = DEFAULT_SUMMARY_LENGTH,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Select an entity summary with the repository's task-decomposed ToT4ES."""
    if not triples:
        return {"summary": [], "selected_indices": [], "summary_length": 0}

    summary_length = max(1, min(int(summary_length), len(triples)))
    all_triples = [_triple_text(triple) for triple in triples]
    frequencies = _predicate_frequencies(all_triples)
    roles = _semantic_roles(all_triples)
    llm = create_chat_client(provider, **({"model": model} if model else {}))
    input_seq = "\n".join(all_triples)

    search = TaskDecomposedToT(
        llm=llm,
        input_seq=input_seq,
        get_relatedness_prompt=make_relatedness_prompt(
            entity_label, all_triples, predicate_frequencies=frequencies, dataset_name="dbpedia"
        ),
        get_informativeness_prompt=make_informativeness_prompt(
            entity_label, all_triples, predicate_frequencies=frequencies, dataset_name="dbpedia"
        ),
        get_diversity_prompt=make_diversity_prompt(
            entity_label, all_triples, semantic_roles=roles, dataset_name="dbpedia"
        ),
        get_state_eval_prompt=make_combined_evaluation_prompt(entity_label, all_triples),
        heuristic_calculator=entity_heuristic_calculator,
        num_triples=len(all_triples),
    )
    search.n_steps = summary_length
    search.n_candidates_per_task = 2
    search.n_evals = 3
    search.breadth_limit = 3
    search.thought_temperature = 0.8
    search.eval_temperature = 0.3
    search.do_sample = None

    best_state = search.bfs(verbose=False, event_callback=event_callback)
    selected_indices = [
        int(value) for value in best_state.splitlines() if value.strip().isdigit()
    ]
    selected_triples = decode_state_to_triples(best_state, all_triples)
    selected = []
    for index, triple_text in zip(selected_indices, selected_triples):
        source = triples[index - 1]
        selected.append({**source, "summary_index": index, "triple_text": triple_text})

    return {
        "summary": selected,
        "selected_indices": selected_indices,
        "summary_length": len(selected),
        "provider": getattr(llm, "provider_id", None) or type(llm).__name__,
        "model": llm.model_id,
    }
