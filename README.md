# file-agent

`file-agent` is a simple production-style Python project that uses a Groq-hosted model to understand a user's request, choose the correct file tool, and execute file operations through a CLI.

The agent currently supports:
- Creating files
- Reading files
- Appending to files

All managed files are stored inside the `data/` directory.

## Project structure

```text
file-agent/
  app/
    agent/
      agent.py
      prompt.py
      planner.py
    tools/
      file_tools.py
      tool_registry.py
    core/
      llm.py
      config.py
    main.py
  data/
  requirements.txt
  README.md
```

## Requirements

- Python 3.10+
- A Groq API key in the `.env` file

## Setup

```bash
cd file-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create or update `.env` in the project root:

```bash
GROQ_API_KEY="your_groq_api_key_here"
```

## Run

```bash
cd file-agent
python app/main.py
```

## Example commands

- `create file notes.txt with hello`
- `read notes.txt`
- `append notes.txt with world`

## How it works

1. The CLI accepts a user request.
2. The agent sends the request to the LLM with a strict JSON-only system prompt.
3. The model chooses a tool and returns tool arguments.
4. The agent validates the JSON response.
5. The selected file tool runs against the `data/` directory.

The project uses the OpenAI-compatible Python client to talk to the Groq API.

## Safety notes

- Unsafe paths such as `../secret.txt` are blocked.
- File reads and writes are limited to the `data/` folder.
- Missing files return a clear error message.
