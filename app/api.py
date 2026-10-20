import logging
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app.agent.agent import run_agent
from app.core.config import MISSING_KEY_MESSAGE, has_groq_api_key
from app.tools.tool_registry import TOOLS

logger = logging.getLogger("file_agent")


class RunRequest(BaseModel):
    user_input: str = Field(..., min_length=1, max_length=4000, examples=["read notes.txt"])


class RunResponse(BaseModel):
    response: str


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not has_groq_api_key():
        logger.warning("%s The /run endpoint will return 503 until it is set.", MISSING_KEY_MESSAGE)
    yield


app = FastAPI(title="File Agent", version="1.0.0", lifespan=lifespan)


@app.get("/")
def home():
    return {"message": "File Agent Running 🚀"}


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
