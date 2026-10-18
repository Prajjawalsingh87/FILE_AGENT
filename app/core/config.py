import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

MISSING_KEY_MESSAGE = (
    "GROQ_API_KEY is not set. Copy .env.example to .env and add your key, "
    "or export GROQ_API_KEY in your environment."
)


def has_groq_api_key() -> bool:
    return bool(os.getenv("GROQ_API_KEY", "").strip())


def get_groq_api_key() -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ValueError(MISSING_KEY_MESSAGE)
    return api_key
