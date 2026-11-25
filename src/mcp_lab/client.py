"""Call a tool on the equipment MCP server and print its result as JSON."""

import argparse
import asyncio
import json
import sys
from typing import Any
from uuid import uuid4

from mcp import Client


def prepare_review(employee_id: str, request: str, reason: str) -> dict[str, str]:
    """Create the arguments for one new review, ready to save and replay."""
    fields = {"employee_id": employee_id, "request": request, "reason": reason}
    if not all(isinstance(value, str) and value.strip() for value in fields.values()):
        raise ValueError("Employee ID, request, and reason must be nonempty strings.")
    return {**fields, "request_id": f"REQ-{uuid4().hex}"}


async def _call_tool(url: str, tool: str, arguments: dict[str, Any]) -> int:
    async with asyncio.timeout(10):
        async with Client(url) as client:
            response = await client.call_tool(tool, arguments)

    result = response.structured_content
    if result is None:
        result = [block.model_dump(mode="json") for block in response.content]

    output = sys.stderr if response.is_error else sys.stdout
    print(json.dumps(result, indent=2), file=output)
    return 1 if response.is_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tool",
        help="MCP tool name, or prepare-review to assign a new request ID locally.",
    )
    parser.add_argument("arguments", nargs="?", default="{}", help="JSON object.")
    parser.add_argument("--url", default="http://127.0.0.1:8001/mcp")
    args = parser.parse_args()

    try:
        arguments = json.loads(args.arguments)
    except json.JSONDecodeError as error:
        parser.error(f"Invalid JSON arguments: {error.msg}")
    if not isinstance(arguments, dict):
        parser.error("Arguments must be a JSON object.")

    if args.tool == "prepare-review":
        try:
            prepared = prepare_review(**arguments)
        except (TypeError, ValueError) as error:
            parser.error(str(error))
        print(json.dumps(prepared))
        return 0

    try:
        return asyncio.run(_call_tool(args.url, args.tool, arguments))
    except TimeoutError:
        print(f"MCP call timed out at {args.url}", file=sys.stderr)
    except Exception as error:
        print(f"MCP call failed: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
