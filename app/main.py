from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.agent import run_agent




def main() -> None:
    print("File Agent CLI. Type 'exit' to quit.")

    while True:
        user_input = input(">>> ").strip()

        if user_input.lower() == "exit":
            print("Goodbye.")
            break

        if not user_input:
            print("Please enter a command.")
            continue

        result = run_agent(user_input)
        print(result)


if __name__ == "__main__":
    main()
