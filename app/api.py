import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.agent.agent import run_agent
from app.agent.chat import LLMUnavailableError, run_chat
from app.core.config import MISSING_KEY_MESSAGE, has_groq_api_key
from app.core.llm import call_llm_messages
from app.search import MAX_LIMIT, MAX_OFFSET, MAX_QUERY_LENGTH, get_search
from app.storage import MAX_UPLOAD_BYTES, UploadError, get_store
from app.tools.tool_registry import TOOLS

logger = logging.getLogger("file_agent")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class RunRequest(BaseModel):
    user_input: str = Field(..., min_length=1, max_length=4000, examples=["read notes.txt"])


class RunResponse(BaseModel):
    response: str


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=20_000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, examples=["find files about milk"])
    history: list[ChatTurn] = Field(
        default_factory=list,
        max_length=50,
        description="Earlier turns, oldest first. The server keeps only the most recent "
        "turns that fit its token budget.",
    )


class ToolCallModel(BaseModel):
    tool: str
    args: dict
    summary: str
    ok: bool


class ChatResponse(BaseModel):
    reply: str
    ok: bool
    tool_calls: list[ToolCallModel]
    history_turns_used: int


class FileInfo(BaseModel):
    name: str
    size: int
    modified: float


class FileListResponse(BaseModel):
    files: list[FileInfo]
    usage: dict


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


app = FastAPI(title="File Agent", version="1.1.0", lifespan=lifespan)

# Multipart overhead allowance on top of the per-file limit.
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + 64_000


@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    """Reject oversized uploads from Content-Length before the body is parsed."""
    if request.method == "POST" and request.url.path == "/files":
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_UPLOAD_REQUEST_BYTES):
            return JSONResponse(
                status_code=413,
                content={"detail": f"Upload is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB."},
            )
    return await call_next(request)


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


@app.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest):
    """One conversational turn. The client sends recent history; nothing is stored server-side."""
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message must not be blank.")
    if not has_groq_api_key():
        raise HTTPException(status_code=503, detail=MISSING_KEY_MESSAGE)
    history = [turn.model_dump() for turn in body.history]
    try:
        result = run_chat(message, history, llm=call_llm_messages)
    except LLMUnavailableError as exc:
        logger.warning("chat LLM failure: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ChatResponse(**result.to_dict())


@app.get("/files", response_model=FileListResponse)
def list_files():
    """Files in data/ (top level, hidden files excluded) plus storage usage and limits."""
    store = get_store()
    return FileListResponse(files=[f.to_dict() for f in store.list()], usage=store.usage())


@app.post("/files", status_code=201, response_model=FileInfo)
async def upload_file(
    file: UploadFile = File(..., description="UTF-8 text file, max 1 MB"),
    overwrite: bool = Query(False, description="Replace an existing file with the same name"),
):
    """Upload a text file into data/. It becomes searchable immediately."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    try:
        stored = get_store().save(file.filename, data, overwrite=overwrite)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return FileInfo(**stored.to_dict())


@app.delete("/files/{name}", status_code=204)
def delete_file(name: str):
    try:
        get_store().delete(name)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
