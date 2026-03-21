from openai import OpenAI

from app.core.config import get_groq_api_key


def call_llm(prompt: str) -> str:
    client = OpenAI(
        api_key=get_groq_api_key(),
        base_url="https://api.groq.com/openai/v1",
    )
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return (response.choices[0].message.content or "").strip()
