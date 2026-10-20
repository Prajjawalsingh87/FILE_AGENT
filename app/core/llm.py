from openai import OpenAI

from app.core.config import GROQ_BASE_URL, GROQ_MODEL, LLM_TIMEOUT_SECONDS, get_groq_api_key


def call_llm_messages(messages: list[dict[str, str]]) -> str:
    """Send a chat-style message list and return the model's JSON text."""
    client = OpenAI(
        api_key=get_groq_api_key(),
        base_url=GROQ_BASE_URL,
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=2,
    )
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        response_format={"type": "json_object"},
    )
    return (response.choices[0].message.content or "").strip()


def call_llm(prompt: str) -> str:
    return call_llm_messages([{"role": "user", "content": prompt}])
