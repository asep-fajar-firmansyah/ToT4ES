# ToT4ES Web UI

Code pool for the web interface. A user types an entity name, the backend
resolves it against the public DBpedia SPARQL endpoint and returns the entity
description plus 30 triples.

## Layout

| Path | Purpose |
| --- | --- |
| `app.py` | FastAPI backend and static file serving |
| `dbpedia_client.py` | SPARQL queries: name resolution, description, triples |
| `llm_api.py` | Chat adapters: DICE LLM API (OpenAI-compatible) and local Ollama |
| `tot4es_service.py` | Existing task-decomposed ToT4ES search wired to the selected adapter |
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

### Serve on the machine's network address

`--host 127.0.0.1` (the uvicorn default) only accepts local connections. Bind to
all interfaces to reach the app at `http://131.234.28.226:8000`:

```bash
cd web_ui
uvicorn app:app --host 0.0.0.0 --port 8000
```

- Drop `--reload` for anything other than development.
- The port must be open in the host firewall, e.g.
  `sudo ufw allow 8000/tcp`.
- To use `http://131.234.28.226` without a port, either run on port 80
  (`--port 80` needs root or `sudo setcap 'cap_net_bind_service=+ep' $(which python3)`)
  or put nginx/Apache in front and proxy to `127.0.0.1:8000`. Behind a reverse
  proxy add `--proxy-headers --forwarded-allow-ips='*'`.
- The frontend calls the API with relative paths, so no client-side URL change
  is needed and no CORS configuration is required.
- Exposing the server also exposes `/api/summarize`, which spends your LLM
  quota. Keep it on a trusted network or place authentication in front of it,
  and never move `DICE_LLM_API_KEY` into the browser.

## LLM providers

The UI has an **LLM** dropdown next to *Generate ToT4ES summary* with two
backends. Providers that are not usable (no API key, Ollama not running) are
shown as unavailable.

### 1. DICE LLM API (remote, default)

| Variable | Default |
| --- | --- |
| `DICE_LLM_API_KEY` | *(required)* |
| `DICE_LLM_ENDPOINT` | `https://dice-llm-api.cs.uni-paderborn.de/v1/chat/completions` |
| `DICE_LLM_MODEL` | `general-purpose` |
| `DICE_LLM_TIMEOUT` | `300` |

### 2. Ollama (local, no API key)

```bash
ollama pull qwen3.5:0.8b
ollama serve            # usually already running as a service
```

| Variable | Default |
| --- | --- |
| `OLLAMA_ENDPOINT` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `qwen3.5:0.8b` |
| `OLLAMA_TIMEOUT` | `300` |
| `OLLAMA_THINK` | `false` — reasoning preambles exhaust the small ToT4ES token budgets |
| `OLLAMA_MIN_PREDICT` | `256` — floor for `num_predict`, prevents truncated answers |
| `OLLAMA_VERBOSE` | falls back to `DICE_LLM_VERBOSE` |

Set `LLM_PROVIDER=ollama` to make Ollama the preselected provider:

```bash
cd web_ui
export LLM_PROVIDER=ollama
export OLLAMA_MODEL=qwen3.5:0.8b
uvicorn app:app --reload --port 8000
```

Requests hit Ollama's native `/api/chat` with `stream: false` and
`think: false` (retried without the field if the model rejects it);
`temperature` and `num_predict` are mapped from the ToT4ES search settings,
`n > 1` is emulated with sequential calls, and `<think>` blocks, code fences
and reasoning preambles are stripped so the evaluator receives plain JSON.

## API

| Endpoint | Description |
| --- | --- |
| `GET /api/search?q=<name>&limit=10` | Candidate DBpedia resources for a name |
| `GET /api/entity?name=<name>&limit=30` | Description + triples resolved from a name |
| `GET /api/entity?uri=<uri>&limit=30` | Description + triples for an explicit resource URI |
| `GET /api/providers` | Available LLM backends, their models and readiness |
| `POST /api/summarize` | Run task-decomposed ToT4ES over the returned triples |

The summary endpoint accepts JSON with `entity_label`, `triples`, optional
`summary_length` (1-20), optional `provider` (`dice` or `ollama`) and optional
`model` to override the provider default. Without `provider` it falls back to
`LLM_PROVIDER`, then to the DICE API.
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
