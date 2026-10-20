# File Agent

A small LLM-powered file agent. You describe what you want in plain English
("create file notes.txt with hello"), a Groq-hosted Llama model picks the right
tool and arguments as strict JSON, and the agent runs that tool against a
sandboxed `data/` directory. It ships as both an interactive CLI and a FastAPI
service.

Live instance (Render): <https://file-agent.onrender.com/>

## Features

- Create, read and append files using natural language
- **Full-text search** over `data/` with pure-Python BM25 ranking (`GET /search`),
  highlighted snippets and pagination; also available to the agent as the `search_files` tool
- **Web UI** at `/` (plain HTML/JS, no build step) to run the agent and search files
- Strict JSON tool-calling with validation of the model's output
- Path sandboxing: absolute paths and `..` traversal are rejected, all I/O stays in `data/`
- CLI (`app/main.py`) and HTTP API (`app/api.py`) entry points
- Clear errors when `GROQ_API_KEY` is missing (CLI exits, API returns `503`)
- Dockerfile (non-root, slim base) and GitHub Actions CI

## Architecture

```mermaid
flowchart LR
  U[User] -->|CLI input or POST /run| A[run_agent]
  A --> P[planner + SYSTEM_PROMPT]
  P --> L[call_llm<br/>Groq OpenAI-compatible API]
  L -->|"{tool, args}" JSON| V[validate response]
  V --> R[tool_registry]
  R --> T1[create_file]
  R --> T2[read_file]
  R --> T3[append_file]
  R --> T4[search_files]
  T1 & T2 & T3 --> D[(data/ sandbox)]
  T4 --> S[BM25 index<br/>app/search] --> D
  W[GET /search] --> S
```

1. The CLI or API receives a request.
2. `run_agent` builds a prompt from the system prompt and the request.
3. The LLM returns `{"tool": ..., "args": {...}}` (JSON mode is enforced).
4. The response is validated and the tool is looked up in the registry.
5. The tool runs inside `data/` and its result is returned.

## Requirements

- Python 3.10+
- A Groq API key (<https://console.groq.com/keys>)

## Setup

```bash
git clone https://github.com/Prajjawalsingh87/FILE_AGENT.git
cd FILE_AGENT
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then put your GROQ_API_KEY in .env
```

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | yes | — | Groq API key |
| `GROQ_MODEL` | no | `llama-3.3-70b-versatile` | Model used for tool selection |
| `LLM_TIMEOUT_SECONDS` | no | `30` | Per-request LLM timeout |
| `PORT` | no | `8000` | Port used by the Docker image |

## Run

### CLI

```bash
python app/main.py
```

```text
File Agent CLI. Type 'exit' to quit.
>>> create file todo.txt with buy milk
Created file: todo.txt
>>> read todo.txt
buy milk
```

### API

```bash
uvicorn app.api:app --reload --port 8000
```

Web UI: <http://localhost:8000/> (add `?q=words` to prefill and run a search).
Interactive docs: <http://localhost:8000/docs>

```bash
curl -s http://localhost:8000/health
curl -s 'http://localhost:8000/search?q=hello&limit=5'
curl -s -X POST http://localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"user_input": "read notes.txt"}'
```

### Docker

```bash
docker build -t file-agent .
docker run --rm -p 8000:8000 --env-file .env file-agent
```

## API reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Single-page web UI (HTML) for running the agent and searching |
| `GET` | `/health` | `{"status": "ok", "llm_configured": true/false}` |
| `GET` | `/tools` | Names and descriptions of the agent's tools |
| `GET` | `/search?q=&limit=&offset=` | BM25-ranked full-text search over `data/` |
| `POST` | `/run` | Body `{"user_input": "..."}` → `{"response": "..."}` |

`POST /run` still accepts the older `?user_input=...` query parameter for
backward compatibility, but the JSON body is preferred. It returns `422` when
no input is given and `503` when `GROQ_API_KEY` is not configured.

## Search

`GET /search` works like a tiny Elasticsearch over the files in `data/`, without
any extra dependency (`app/search/`):

- **Tokenizing**: lowercase Unicode word tokens; punctuation and `_` split words;
  common English stopwords are dropped (unless the query is only stopwords).
- **Inverted index**: `term -> {document: term frequency}`. Words in the file
  path count as extra occurrences, so `notes` finds `notes.txt`.
- **Ranking**: Okapi BM25 (`k1=1.2`, `b=0.75`); ties are broken by path.
- **Snippets**: a ~200 character window around the densest cluster of matched
  terms. Text is HTML-escaped and matches are wrapped in `<mark>`.
- **Freshness**: before each query the directory is re-scanned with `stat`
  only; if any path, size or mtime changed, the index is rebuilt. Files the
  agent creates or appends to are searchable immediately.
- **Limits**: `q` 1–256 chars (max 32 terms), `limit` 1–50 (default 10),
  `offset` 0–1000. Hidden files, symlinks, binary files and files over 1 MB
  are skipped; at most 5,000 files are indexed.

```json
{
  "query": "hello prajjawal", "total": 1, "limit": 10, "offset": 0, "took_ms": 0.4,
  "hits": [
    {"path": "test.txt", "score": 1.4313,
     "snippet": "<mark>Hello</mark> <mark>Prajjawal</mark>",
     "matched_terms": ["hello", "prajjawal"]}
  ]
}
```

The agent can use the same index through the `search_files` tool, e.g.
"find files that mention milk".

## Example commands

- `create file notes.txt with hello`
- `read notes.txt`
- `append notes.txt with world`
- `search files for hello`

## Project structure

```text
FILE_AGENT/
├── app/
│   ├── agent/
│   │   ├── agent.py          # orchestration: prompt → LLM → validate → tool
│   │   ├── planner.py        # placeholder for multi-step planning
│   │   └── prompt.py         # strict JSON system prompt
│   ├── core/
│   │   ├── config.py         # env loading (.env), model settings
│   │   └── llm.py            # Groq client (OpenAI-compatible)
│   ├── search/
│   │   ├── bm25.py           # tokenizer, inverted index, BM25, snippets
│   │   └── index.py          # data/ scanner, rebuild on file changes
│   ├── static/index.html     # web UI served at /
│   ├── tools/
│   │   ├── file_tools.py     # sandboxed create/read/append
│   │   ├── search_tools.py   # search_files agent tool
│   │   └── tool_registry.py  # tool lookup by name
│   ├── api.py                # FastAPI app
│   └── main.py               # interactive CLI
├── data/                     # sandbox for all file operations
├── .github/workflows/ci.yml  # lint, compile, API smoke test, docker build
├── Dockerfile
├── .env.example
├── requirements.txt
└── ruff.toml
```

## Deployment (Render)

The live instance runs on Render. For a native Python web service use:

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.api:app --host 0.0.0.0 --port $PORT`
- Environment: set `GROQ_API_KEY` in the Render dashboard (never commit it)

Alternatively, point Render at the `Dockerfile`; it honours the `PORT` variable.
Note that files written to `data/` live on the container's ephemeral disk and
are lost on redeploy unless you attach a persistent disk.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `GROQ_API_KEY is not set` / HTTP `503` | Create `.env` from `.env.example` or export the variable. |
| `Error: Tool execution failed. Error code: 401` | The key is invalid or revoked; generate a new one in the Groq console. |
| `ModuleNotFoundError: No module named 'app'` | Run commands from the repo root (`uvicorn app.api:app`, `python app/main.py`). |
| `Error: LLM returned invalid JSON` | Rephrase the request; the model occasionally ignores JSON mode. |
| `Unsafe paths are not allowed` | Use a plain relative file name; `..` and absolute paths are blocked by design. |
| `422` from `/run` | Send `Content-Type: application/json` with a `user_input` field. |

## Safety notes

- File reads and writes are limited to `data/`.
- `.env` is git-ignored; never commit API keys.
- The container runs as an unprivileged user.


## October 2026 improvements

`GET /tools` lists supported tool names and descriptions without touching files. Both JSON and legacy query inputs to `/run` are limited to 4,000 characters. API regression tests cover discovery, oversized input and blank input.


Run `python -m unittest discover -s tests` after installing the requirements and
`httpx`. The service remains suited to the existing Render/container deployment.
Vercel's ephemeral function filesystem does not provide the persistent, shared
`data/` workspace this API expects. Do not move file state there without a storage redesign.
