import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.agent import run_agent  # noqa: E402
from app.core.config import MISSING_KEY_MESSAGE, has_groq_api_key  # noqa: E402


def main() -> None:
    if not has_groq_api_key():
        print(f"Error: {MISSING_KEY_MESSAGE}", file=sys.stderr)
        sys.exit(1)

    print("File Agent CLI. Type 'exit' to quit.")

    while True:
        try:
            user_input = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if user_input.lower() in {"exit", "quit"}:
            print("Goodbye.")
            break

        if not user_input:
            print("Please enter a command.")
            continue

        result = run_agent(user_input)
        print(result)


if __name__ == "__main__":
    main()
