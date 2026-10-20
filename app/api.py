import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agent.agent import run_agent
from app.core.config import MISSING_KEY_MESSAGE, has_groq_api_key
from app.search import MAX_LIMIT, MAX_OFFSET, MAX_QUERY_LENGTH, get_search
from app.tools.tool_registry import TOOLS

logger = logging.getLogger("file_agent")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class RunRequest(BaseModel):
    user_input: str = Field(..., min_length=1, max_length=4000, examples=["read notes.txt"])


class RunResponse(BaseModel):
    response: str


class SearchHitModel(BaseModel):
    path: str
    score: float
    snippet: str = Field(description="HTML-escaped excerpt; matches wrapped in <mark>.")
    matched_terms: list[str]


class SearchResponse(BaseModel):
    query: str
    total: int
    limit: int
    offset: int
    took_ms: float
    hits: list[SearchHitModel]


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not has_groq_api_key():
        logger.warning("%s The /run endpoint will return 503 until it is set.", MISSING_KEY_MESSAGE)
    yield


app = FastAPI(title="File Agent", version="1.0.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def home():
    """Single-page web UI for running the agent and searching files."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/health")
def health():
    return {"status": "ok", "llm_configured": has_groq_api_key()}


@app.get("/tools")
def tools():
    """Describe supported operations without reading or changing user files."""
    return {"tools": [{"name": tool.name, "description": tool.description} for tool in TOOLS]}


@app.post("/run", response_model=RunResponse)
def run(
    body: RunRequest | None = Body(default=None),
    user_input: str | None = Query(
        default=None,
        max_length=4000,
        description='Deprecated: send a JSON body {"user_input": ...} instead.',
    ),
):
    text = (body.user_input if body else user_input or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Provide 'user_input' in the JSON body.")
    if not has_groq_api_key():
        raise HTTPException(status_code=503, detail=MISSING_KEY_MESSAGE)

    return RunResponse(response=run_agent(text))


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search text"),
    limit: int = Query(10, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
):
    """BM25-ranked full-text search over files in data/ (index refreshes on file changes)."""
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="Query 'q' must not be blank.")
    started = time.perf_counter()
    total, hits = get_search().search(query, limit=limit, offset=offset)
    return SearchResponse(
        query=query,
        total=total,
        limit=limit,
        offset=offset,
        took_ms=round((time.perf_counter() - started) * 1000, 2),
        hits=[SearchHitModel(**hit.to_dict()) for hit in hits],
    )
