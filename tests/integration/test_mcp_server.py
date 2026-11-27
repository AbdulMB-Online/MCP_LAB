"""Smoke test the sample server's real TCP entry point."""

import asyncio

import pytest
from mcp import Client


@pytest.mark.socket
def test_server_exposes_working_ping_tool(serve_module) -> None:
    async def check_ping(url: str) -> object:
        async with asyncio.timeout(10), Client(url) as client:
            response = await client.call_tool("ping", {})
            return response.is_error, response.structured_content

    with serve_module("mcp_lab.minimal.sampleserver") as url:
        result = asyncio.run(check_ping(url))

    assert result == (False, {"result": "pong"})
