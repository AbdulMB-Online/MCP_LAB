"""Run the first equipment request against the Streamable HTTP MCP server."""

import argparse
import asyncio
import json
import sys
from typing import Any

from mcp import Client

EMPLOYEE_ID = "EMP-001"
REQUEST = "Can I get my first work laptop?"


async def run_first_request(client: Client) -> dict[str, Any]:
    """Read the employee record for the first equipment request."""
    result = await client.call_tool("get_employee_info", {"employee_id": EMPLOYEE_ID})
    if result.is_error or result.structured_content is None:
        raise ValueError(f"Employee lookup did not return a record for {EMPLOYEE_ID}.")
    return result.structured_content


async def _run(url: str) -> None:
    async with asyncio.timeout(10), Client(url) as client:
        employee = await run_first_request(client)
    print(json.dumps({"request": REQUEST, "employee": employee}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001/mcp")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.url))
    except TimeoutError:
        print(f"Employee lookup timed out at {args.url}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Employee lookup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
