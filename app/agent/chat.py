"""Conversation handling for the chat UI.

The browser keeps the conversation (per tab session) and sends the most recent
turns with each message. The server never stores conversations; it trims the
history to ``MAX_HISTORY_TURNS`` turns and roughly ``MAX_HISTORY_TOKENS`` tokens
(most recent first), sends it to the LLM with the chat system prompt, runs the
single tool the model picks and reports what it did.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from app.agent.prompt import CHAT_SYSTEM_PROMPT
from app.tools.tool_registry import get_tool_by_name

MAX_HISTORY_TURNS = 12
MAX_HISTORY_TOKENS = 2_000
MAX_TURN_CHARS = 2_000
MAX_REPLY_CHARS = 8_000

LLMCall = Callable[[list[dict[str, str]]], str]


class LLMUnavailableError(RuntimeError):
    """The LLM request itself failed (network, auth, rate limit, timeout)."""


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 characters per token), good enough for a budget."""
    return max(1, math.ceil(len(text) / 4)) if text else 0


def trim_history(
    history: list[dict[str, str]],
    max_turns: int = MAX_HISTORY_TURNS,
    max_tokens: int = MAX_HISTORY_TOKENS,
) -> list[dict[str, str]]:
    """Keep the newest valid turns that fit in both the turn and token budgets.

    Unknown roles and empty turns are dropped; long turns are truncated to
    ``MAX_TURN_CHARS``. Order is preserved (oldest first).
    """
    kept: list[dict[str, str]] = []
    budget = max_tokens
    for turn in reversed(history or []):
        if len(kept) >= max_turns:
            break
        role = turn.get("role") if isinstance(turn, dict) else None
        content = turn.get("content") if isinstance(turn, dict) else None
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        if len(content) > MAX_TURN_CHARS:
            content = content[:MAX_TURN_CHARS] + " …[truncated]"
        cost = estimate_tokens(content)
        if cost > budget:
            break
        budget -= cost
        kept.append({"role": role, "content": content})
    kept.reverse()
    return kept


def build_messages(message: str, history: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"role": "system", "content": CHAT_SYSTEM_PROMPT}, *history,
            {"role": "user", "content": message}]


def describe_tool_call(tool: str, args: dict[str, Any]) -> str:
    """Human-readable label such as 'searched files for "milk"' or 'read notes.txt'."""
    name = str(args.get("name", "")).strip() or "a file"
    if tool == "search_files":
        return f'searched files for "{str(args.get("query", "")).strip()}"'
    if tool == "read_file":
        return f"read {name}"
    if tool == "create_file":
        return f"created {name}"
    if tool == "append_file":
        return f"appended to {name}"
    return f"ran {tool}"


@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any]
    summary: str
    ok: bool


@dataclass
class ChatResult:
    reply: str
    ok: bool = True
    tool_calls: list[ToolCall] = field(default_factory=list)
    history_turns_used: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _parse(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object.")
    return payload


def _clip(text: str) -> str:
    if len(text) <= MAX_REPLY_CHARS:
        return text
    return text[:MAX_REPLY_CHARS] + f"\n…[truncated, {len(text) - MAX_REPLY_CHARS} more chars]"


def run_chat(message: str, history: list[dict[str, str]], llm: LLMCall) -> ChatResult:
    """Run one conversational turn. Raises LLMUnavailableError if the LLM call fails."""
    trimmed = trim_history(history)
    messages = build_messages(message.strip(), trimmed)
    try:
        raw = llm(messages)
    except Exception as exc:  # network/auth/timeout errors from the client
        raise LLMUnavailableError(f"LLM request failed: {exc}") from exc

    used = len(trimmed)
    try:
        payload = _parse(raw)
    except ValueError as exc:
        return ChatResult(reply=f"Error: {exc}", ok=False, history_turns_used=used)

    reply = payload.get("reply")
    if "tool" not in payload and isinstance(reply, str) and reply.strip():
        return ChatResult(reply=_clip(reply.strip()), history_turns_used=used)

    tool_name = payload.get("tool")
    args = payload.get("args", {})
    if not isinstance(tool_name, str) or not tool_name.strip():
        return ChatResult(
            reply="Error: LLM response is missing a valid 'tool' value.",
            ok=False,
            history_turns_used=used,
        )
    if not isinstance(args, dict):
        args = {}
    tool_name = tool_name.strip()
    call = ToolCall(tool_name, args, describe_tool_call(tool_name, args), ok=False)

    tool = get_tool_by_name(tool_name)
    if tool is None:
        return ChatResult(
            reply=f"Error: Tool '{tool_name}' is not registered.",
            ok=False,
            tool_calls=[call],
            history_turns_used=used,
        )
    try:
        result = str(tool.func(**args))
    except TypeError as exc:
        result = f"Error: Invalid tool arguments. {exc}"
    except Exception as exc:
        result = f"Error: {exc}"
    else:
        call.ok = True
    return ChatResult(
        reply=_clip(result), ok=call.ok, tool_calls=[call], history_turns_used=used
    )
