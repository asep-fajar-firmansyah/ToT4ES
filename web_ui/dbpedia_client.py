#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DBpedia SPARQL client for the ToT4ES web UI.

Given an entity name typed by the user, resolve it to a DBpedia resource,
fetch its description (dbo:abstract / rdfs:comment) and retrieve its triples.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from SPARQLWrapper import JSON, SPARQLWrapper

DBPEDIA_ENDPOINT = "https://dbpedia.org/sparql"
DBPEDIA_RESOURCE_PREFIX = "http://dbpedia.org/resource/"
DEFAULT_TRIPLE_LIMIT = 30
DEFAULT_SEARCH_LIMIT = 10
REQUEST_TIMEOUT = 30

PREFIXES = """
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX dbo: <http://dbpedia.org/ontology/>
PREFIX dbr: <http://dbpedia.org/resource/>
"""

# Verbose predicates that add no value to a summary and blow up the payload.
EXCLUDED_PREDICATES = [
    "http://dbpedia.org/ontology/abstract",
    "http://dbpedia.org/ontology/description",
    "http://www.w3.org/2000/01/rdf-schema#comment",
    "http://dbpedia.org/ontology/wikiPageWikiLink",
    "http://dbpedia.org/ontology/wikiPageExternalLink",
    "http://dbpedia.org/ontology/wikiPageID",
    "http://dbpedia.org/ontology/wikiPageRevisionID",
    "http://dbpedia.org/ontology/wikiPageLength",
    "http://dbpedia.org/property/wikiPageUsesTemplate",
]

# Keeps only English content: language-tagged literals must be @en (untagged and
# typed literals such as dates/numbers pass through), and object resources must
# not point at a non-English DBpedia chapter or Wikipedia edition.
ENGLISH_ONLY_FILTER = """
            FILTER (!isLiteral(?o) || lang(?o) = "" || langMatches(lang(?o), "en"))
            FILTER (!isURI(?o) || !REGEX(STR(?o), "^https?://[a-z][a-z][a-z]?(-[a-z]+)?\\\\.dbpedia\\\\.org/", "i"))
            FILTER (!isURI(?o)
                    || !REGEX(STR(?o), "^https?://[a-z][a-z][a-z]?(-[a-z]+)?\\\\.wikipedia\\\\.org/", "i")
                    || REGEX(STR(?o), "^https?://en\\\\.wikipedia\\\\.org/", "i"))
"""

_URI_PATTERN = re.compile(r"^https?://[^\s<>\"{}|\\^`]+$")
# Keep only characters that are safe inside a Virtuoso free-text search string.
_UNSAFE_TERM_CHARS = re.compile(r"[^\w\s\-.']", re.UNICODE)
_NT_LINE = re.compile(r'^\s*(<[^>]+>)\s+(<[^>]+>)\s+(.+?)\s*\.\s*$')
_NT_LITERAL = re.compile(r'^"((?:\\.|[^"\\])*)"(?:@[^\s]+|\^\^<[^>]+>)?$')


class DBpediaError(RuntimeError):
    """Raised when the DBpedia endpoint cannot be queried or returns garbage."""


def sanitize_term(name: str) -> str:
    """Strip every character that could break out of a SPARQL string literal."""
    cleaned = _UNSAFE_TERM_CHARS.sub(" ", name or "")
    cleaned = cleaned.replace("'", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def validate_uri(uri: str) -> str:
    """Validate that `uri` is a plain http(s) URI safe to inline in a query."""
    candidate = (uri or "").strip()
    if not _URI_PATTERN.match(candidate):
        raise ValueError(f"Invalid resource URI: {uri!r}")
    return candidate


def name_to_resource_uri(name: str) -> Optional[str]:
    """Build the canonical DBpedia resource URI for a plain entity name."""
    term = sanitize_term(name)
    if not term:
        return None
    local_name = quote(term.replace(" ", "_"), safe="_-.")
    return f"{DBPEDIA_RESOURCE_PREFIX}{local_name}"


def local_name(uri: str) -> str:
    """Human readable label derived from the last URI segment."""
    if not uri:
        return ""
    fragment = uri.rsplit("#", 1)[-1] if "#" in uri else uri.rstrip("/").rsplit("/", 1)[-1]
    fragment = fragment.replace("_", " ")
    fragment = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", fragment)
    return fragment.strip()


def _parse_nt_line(line: str) -> Optional[Tuple[str, str, str, bool]]:
    """Parse one N-Triples statement into URI/literal display values."""
    match = _NT_LINE.match(line)
    if not match:
        return None
    subject, predicate, obj = match.groups()
    subject = subject[1:-1]
    predicate = predicate[1:-1]
    if obj.startswith('<') and obj.endswith('>'):
        return subject, predicate, obj[1:-1], True
    literal = _NT_LITERAL.match(obj)
    if not literal:
        return None
    value = json.loads(f'"{literal.group(1)}"')
    return subject, predicate, value, False


def parse_nt_entity(content: bytes, limit: int = DEFAULT_TRIPLE_LIMIT) -> Dict[str, Any]:
    """Parse an uploaded N-Triples entity description into the web UI schema."""
    triples: List[Dict[str, Any]] = []
    subject_uri: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    label_predicates = {"http://www.w3.org/2000/01/rdf-schema#label", "http://xmlns.com/foaf/0.1/name"}
    description_predicates = {
        "http://dbpedia.org/ontology/abstract",
        "http://dbpedia.org/ontology/description",
        "http://www.w3.org/2000/01/rdf-schema#comment",
    }

    for line_number, raw_line in enumerate(content.decode("utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        parsed = _parse_nt_line(line)
        if parsed is None:
            raise ValueError(f"Invalid N-Triples syntax on line {line_number}.")
        subject, predicate, obj, object_is_uri = parsed
        if subject_uri is None:
            subject_uri = subject
        if subject != subject_uri:
            raise ValueError("The uploaded file must describe one entity subject.")
        if not label and predicate in label_predicates and not object_is_uri:
            label = obj
        if not description and predicate in description_predicates and not object_is_uri:
            description = obj
        if predicate in EXCLUDED_PREDICATES:
            continue
        triples.append({
            "index": len(triples) + 1,
            "subject": subject,
            "subject_label": local_name(subject),
            "predicate": predicate,
            "predicate_label": local_name(predicate),
            "object": obj,
            "object_label": local_name(obj) if object_is_uri else obj,
            "object_is_uri": object_is_uri,
        })

    if not subject_uri or not triples:
        raise ValueError("The uploaded .nt file contains no usable triples.")
    triples = triples[: max(1, min(int(limit), 200))]
    return {
        "query": subject_uri,
        "resolved": {
            "uri": subject_uri,
            "label": label or local_name(subject_uri),
            "abstract": description,
            "comment": None,
            "short_description": None,
            "description": description,
        },
        "candidates": [],
        "triples": triples,
        "source": "upload",
    }


def _run_query(query: str, endpoint: str = DBPEDIA_ENDPOINT) -> List[Dict[str, Any]]:
    sparql = SPARQLWrapper(endpoint)
    sparql.setReturnFormat(JSON)
    sparql.setTimeout(REQUEST_TIMEOUT)
    sparql.setQuery(PREFIXES + query)
    try:
        results = sparql.query().convert()
    except Exception as exc:  # network / endpoint / parsing failures
        raise DBpediaError(f"DBpedia query failed: {exc}") from exc
    if not isinstance(results, dict):
        raise DBpediaError("Unexpected response format from DBpedia endpoint.")
    return results.get("results", {}).get("bindings", [])


def resource_exists(uri: str) -> bool:
    """True when the resource has at least one outgoing triple."""
    safe_uri = validate_uri(uri)
    rows = _run_query(f"SELECT ?p WHERE {{ <{safe_uri}> ?p ?o }} LIMIT 1")
    return bool(rows)


def search_entities(name: str, limit: int = DEFAULT_SEARCH_LIMIT) -> List[Dict[str, str]]:
    """
    Resolve a free-text entity name to candidate DBpedia resources.

    The canonical `dbr:Entity_Name` guess is probed first, then a full-text
    label search supplies alternatives.
    """
    term = sanitize_term(name)
    if not term:
        return []
    limit = max(1, min(int(limit), 50))

    candidates: List[Dict[str, str]] = []
    seen: set[str] = set()

    exact_uri = name_to_resource_uri(term)
    if exact_uri and resource_exists(exact_uri):
        candidates.append({"uri": exact_uri, "label": local_name(exact_uri), "exact": True})
        seen.add(exact_uri)

    rows = _run_query(
        f"""
        SELECT DISTINCT ?s ?label WHERE {{
            ?s rdfs:label ?label .
            ?label bif:contains "'{term}'" .
            FILTER (langMatches(lang(?label), "en"))
            FILTER (STRSTARTS(STR(?s), "{DBPEDIA_RESOURCE_PREFIX}"))
            FILTER NOT EXISTS {{ ?s dbo:wikiPageRedirects ?redirect }}
        }}
        LIMIT {limit}
        """
    )
    for row in rows:
        uri = row.get("s", {}).get("value", "")
        if not uri or uri in seen:
            continue
        seen.add(uri)
        candidates.append(
            {
                "uri": uri,
                "label": row.get("label", {}).get("value") or local_name(uri),
                "exact": False,
            }
        )
    return candidates[:limit]


def get_description(uri: str) -> Dict[str, Optional[str]]:
    """
    Fetch the English label and description of a resource.

    DBpedia snapshots expose `dbo:abstract`/`rdfs:comment` while the live
    endpoint exposes the short `dbo:description`, so all three are requested.
    """
    safe_uri = validate_uri(uri)
    rows = _run_query(
        f"""
        SELECT ?label ?abstract ?comment ?shortDescription WHERE {{
            OPTIONAL {{ <{safe_uri}> rdfs:label ?label . FILTER (lang(?label) = "en") }}
            OPTIONAL {{ <{safe_uri}> dbo:abstract ?abstract . FILTER (lang(?abstract) = "en") }}
            OPTIONAL {{ <{safe_uri}> rdfs:comment ?comment . FILTER (lang(?comment) = "en") }}
            OPTIONAL {{ <{safe_uri}> dbo:description ?shortDescription . FILTER (lang(?shortDescription) = "en") }}
        }}
        LIMIT 1
        """
    )
    row = rows[0] if rows else {}
    abstract = row.get("abstract", {}).get("value")
    comment = row.get("comment", {}).get("value")
    short_description = row.get("shortDescription", {}).get("value")
    return {
        "uri": safe_uri,
        "label": row.get("label", {}).get("value") or local_name(safe_uri),
        "abstract": abstract,
        "comment": comment,
        "short_description": short_description,
        "description": abstract or comment or short_description,
    }


def get_triples(uri: str, limit: int = DEFAULT_TRIPLE_LIMIT) -> List[Dict[str, Any]]:
    """Retrieve up to `limit` outgoing English triples of the resource."""
    safe_uri = validate_uri(uri)
    limit = max(1, min(int(limit), 200))
    excluded = ", ".join(f"<{predicate}>" for predicate in EXCLUDED_PREDICATES)
    rows = _run_query(
        f"""
        SELECT ?p ?o WHERE {{
            <{safe_uri}> ?p ?o .
            FILTER (?p NOT IN ({excluded}))
{ENGLISH_ONLY_FILTER}
        }}
        LIMIT {limit}
        """
    )

    triples: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        predicate = row.get("p", {}).get("value", "")
        obj = row.get("o", {})
        obj_value = obj.get("value", "")
        is_uri = obj.get("type") == "uri"
        triples.append(
            {
                "index": index,
                "subject": safe_uri,
                "subject_label": local_name(safe_uri),
                "predicate": predicate,
                "predicate_label": local_name(predicate),
                "object": obj_value,
                "object_label": local_name(obj_value) if is_uri else obj_value,
                "object_is_uri": is_uri,
            }
        )
    return triples


def describe_entity(name: str, limit: int = DEFAULT_TRIPLE_LIMIT) -> Dict[str, Any]:
    """End-to-end lookup: name -> resolved resource + description + triples."""
    candidates = search_entities(name)
    if not candidates:
        return {"query": name, "resolved": None, "candidates": [], "triples": []}

    resolved_uri = candidates[0]["uri"]
    description = get_description(resolved_uri)
    return {
        "query": name,
        "resolved": description,
        "candidates": candidates,
        "triples": get_triples(resolved_uri, limit),
    }


if __name__ == "__main__":
    import json
    import sys

    entity_name = " ".join(sys.argv[1:]) or "Albert Einstein"
    print(json.dumps(describe_entity(entity_name), indent=2, ensure_ascii=False))
