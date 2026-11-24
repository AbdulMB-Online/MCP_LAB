"""Verify discovery and invocation against the running minimal MCP server."""

import asyncio

from mcp import Client


async def main() -> None:
    async with Client("http://127.0.0.1:8000/mcp") as client:
        tools = await client.list_tools()
        names = [tool.name for tool in tools.tools]
        print("Registered tools:", names)
        assert "ping" in names

        response = await client.call_tool("ping", {})
        print("Ping response:", response.structured_content)
        assert not response.is_error
        assert response.structured_content == {"result": "pong"}

        print("MCP connection and tool call verified.")


if __name__ == "__main__":
    asyncio.run(main())
