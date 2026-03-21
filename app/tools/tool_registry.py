from dataclasses import dataclass
from typing import Callable

from app.tools.file_tools import append_file, create_file, read_file


@dataclass(frozen=True)
class Tool:
    name: str
    func: Callable
    description: str


TOOLS = [
    Tool(
        name="create_file",
        func=create_file,
        description="Create a file in the data folder using a file name and content.",
    ),
    Tool(
        name="read_file",
        func=read_file,
        description="Read and return the contents of a file from the data folder.",
    ),
    Tool(
        name="append_file",
        func=append_file,
        description="Append content to an existing file in the data folder.",
    ),
]


def get_tool_by_name(name: str) -> Tool | None:
    return next((tool for tool in TOOLS if tool.name == name), None)
