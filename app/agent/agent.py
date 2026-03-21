import json
from typing import Any

from app.agent.planner import plan_request
from app.agent.prompt import SYSTEM_PROMPT
from app.core.llm import call_llm
from app.tools.tool_registry import get_tool_by_name


def _build_prompt(user_input: str) -> str:
    planned_input = plan_request(user_input)
    return f"{SYSTEM_PROMPT}\nUser request: {planned_input}"


def _parse_llm_response(response_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object.")

    tool_name = payload.get("tool")
    args = payload.get("args", {})

    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("LLM response is missing a valid 'tool' value.")

    if not isinstance(args, dict):
        raise ValueError("LLM response 'args' must be a JSON object.")

    return {"tool": tool_name.strip(), "args": args}


def run_agent(user_input: str) -> str:
    try:
        prompt = _build_prompt(user_input)
        llm_response = call_llm(prompt)
        parsed = _parse_llm_response(llm_response)
        tool = get_tool_by_name(parsed["tool"])

        if tool is None:
            return f"Error: Tool '{parsed['tool']}' is not registered."

        result = tool.func(**parsed["args"])
        return str(result)
    except ValueError as exc:
        return f"Error: {exc}"
    except TypeError as exc:
        return f"Error: Invalid tool arguments. {exc}"
    except Exception as exc:
        return f"Error: Tool execution failed. {exc}"
