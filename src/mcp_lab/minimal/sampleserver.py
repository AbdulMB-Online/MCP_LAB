"""Expose equipment request tools through a Streamable HTTP MCP server."""

import os

from mcp.server import MCPServer

server = MCPServer("Equipment Request Lab")


@server.tool()
def ping() -> str:
    """Confirm that the MCP server can respond to a tool call."""
    return "pong"


if __name__ == "__main__":
    server.run(
        transport="streamable-http",
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8000")),
    )
