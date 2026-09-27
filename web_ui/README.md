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

### Load a local `.nt` entity description

The search form also accepts one UTF-8 N-Triples file. The file must contain
one subject, for example:

```nt
<http://example.org/Barack_Obama> <http://www.w3.org/2000/01/rdf-schema#label> "Barack Obama"@en .
<http://example.org/Barack_Obama> <http://dbpedia.org/ontology/birthDate> "1961-08-04" .
<http://example.org/Barack_Obama> <http://dbpedia.org/ontology/birthPlace> <http://example.org/Honolulu> .
```

Choose the file in **Or upload entity description** and click **Load .nt file**.
The uploaded subject, description, triples, graph and ToT4ES summary use the
same UI flow as a DBpedia result. Install `web_ui/requirements.txt` first;
the upload endpoint requires `python-multipart` for FastAPI form parsing.

### Public landing page and LAN-only ToT4ES

The human-evaluation landing page can stay publicly served at
`http://131.234.28.226/`. Run ToT4ES separately on port `8011` and restrict
that port to your LAN. Use the server's LAN interface address for
`<LAN_IP>` and your network's CIDR (for example, `192.168.1.0/24`) for
`<LAN_CIDR>`:

```bash
cd web_ui
uvicorn app:app --host <LAN_IP> --port 8011
```

Allow LAN clients and deny other sources to port 8011 with UFW:

```bash
sudo ufw allow from <LAN_CIDR> to any port 8011 proto tcp
sudo ufw deny 8011/tcp
```

LAN users open `http://<LAN_IP>:8011/`. Keep the existing public landing-page
service on port 80; these firewall rules only target port 8011. Binding to a
LAN interface address and filtering the port at the firewall are both
important: binding to `0.0.0.0` alone does not make a service LAN-only. If the
server has no separate LAN address, bind to `0.0.0.0` only with an equivalent
firewall or network ACL that permits port 8011 from the LAN and blocks it from
the public Internet. Do not add a blanket `ufw allow 8011/tcp` rule.

Drop `--reload` outside development. The frontend uses relative API paths, so
no CORS configuration is needed. Keeping the API LAN-only also protects the
LLM quota used by `/api/summarize`; never put `DICE_LLM_API_KEY` in the
browser.

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
ollama pull qwen3.5:0.8b   # or any tag from https://ollama.com/library
ollama list                # models offered by the dropdown
ollama serve               # usually already running as a service
```

Every model reported by `/api/tags` is listed in the UI dropdown, so a newly
pulled model shows up after a page reload — no restart or config change needed.
`OLLAMA_MODEL` only decides which one is preselected.

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

Both providers run the identical ToT4ES search, but only Ollama receives the
search's sampling settings: `temperature` (0.8 for thought generation, 0.3 for
evaluation), the per-step token budget and `n` samples (sequential calls). The
DICE gateway rejects those fields with HTTP 400, so its request body stays
`{model, messages}` and the gateway's own defaults apply. Reasoning preambles,
`<think>` blocks and code fences are stripped from both.

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
