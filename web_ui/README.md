# ToT4ES Web UI

Code pool for the web interface. A user types an entity name, the backend
resolves it against the public DBpedia SPARQL endpoint and returns the entity
description plus 30 triples.

## Layout

| Path | Purpose |
| --- | --- |
| `app.py` | FastAPI backend and static file serving |
| `dbpedia_client.py` | SPARQL queries: name resolution, description, triples |
| `llm_api.py` | OpenAI-compatible chat API adapter |
| `tot4es_service.py` | Existing task-decomposed ToT4ES search wired to the API adapter |
| `static/` | Frontend (`index.html`, `app.js`, `style.css`) |

## Run

```bash
pip install -r web_ui/requirements.txt
cd web_ui
export DICE_LLM_API_KEY="your-api-key"
export DICE_LLM_VERBOSE="true"
uvicorn app:app --reload --port 8000
```

Open http://127.0.0.1:8000

## API

| Endpoint | Description |
| --- | --- |
| `GET /api/search?q=<name>&limit=10` | Candidate DBpedia resources for a name |
| `GET /api/entity?name=<name>&limit=30` | Description + triples resolved from a name |
| `GET /api/entity?uri=<uri>&limit=30` | Description + triples for an explicit resource URI |
| `POST /api/summarize` | Run task-decomposed ToT4ES over the returned triples |

The summary endpoint accepts JSON with `entity_label`, `triples`, and optional
`summary_length` (1-20). It uses `DICE_LLM_ENDPOINT` and `DICE_LLM_MODEL` when
set, defaulting to the supplied DICE endpoint and `general-purpose` model.
Keep `DICE_LLM_API_KEY` server-side and do not commit it. The bearer token
included in an example request should be revoked and replaced if it is real.

Set `DICE_LLM_VERBOSE=true` to log each LLM request and response in the
uvicorn terminal. Logs include the endpoint, model, prompt messages, token
budget, response status, and response body, but never the authorization header.
Use `DICE_LLM_LOG_MAX_CHARS` to change the response/prompt truncation limit.

## CLI check

```bash
python web_ui/dbpedia_client.py "Albert Einstein"
```

## Notes

- Entity resolution tries the canonical `dbr:Entity_Name` URI first, then falls
  back to a Virtuoso `bif:contains` label search.
- The description is the first available of `dbo:abstract`, `rdfs:comment` and
  `dbo:description` — `https://dbpedia.org/sparql` currently serves DBpedia Live,
  which only exposes the short `dbo:description`.
- User input is sanitized (`sanitize_term`) and URIs are validated
  (`validate_uri`) before being inlined into SPARQL, preventing query injection.
- Verbose predicates (`dbo:abstract`, `dbo:wikiPageWikiLink`, ...) are excluded
  from the triple list; see `EXCLUDED_PREDICATES`.
- All queries are English-only (`ENGLISH_ONLY_FILTER`): language-tagged literals
  must be `@en`, and object resources pointing at non-English DBpedia chapters
  (`fr.dbpedia.org`, ...) or Wikipedia editions are dropped. Untagged and typed
  literals such as dates and numbers are kept.
