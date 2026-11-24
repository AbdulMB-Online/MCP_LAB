"""Exercise the actual MCP server through its Streamable HTTP endpoint."""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from typing import TextIO

import pytest
from mcp import Client


def _wait_for_server(
    process: subprocess.Popen[str], port: int, output: TextIO
) -> None:
    deadline = time.monotonic() + 10

    while time.monotonic() < deadline:
        if process.poll() is not None:
            output.seek(0)
            pytest.fail(f"MCP server exited before listening:\n{output.read()}")

        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)

    output.seek(0)
    pytest.fail(f"MCP server did not start within 10 seconds:\n{output.read()}")


@pytest.fixture
def server_url() -> Iterator[str]:
    """Launch an isolated server and stop it even when the test fails."""
    with socket.socket() as available_port:
        available_port.bind(("127.0.0.1", 0))
        port = available_port.getsockname()[1]

    environment = {
        **os.environ,
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(port),
    }

    with tempfile.TemporaryFile(mode="w+") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "mcp_lab.minimal.sampleserver"],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            _wait_for_server(process, port, output)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def test_server_exposes_working_ping_tool(server_url: str) -> None:
    """A real HTTP client can discover and successfully invoke ping."""

    async def check_ping() -> None:
        async with asyncio.timeout(10):
            async with Client(server_url) as client:
                tools = await client.list_tools()
                assert "ping" in {tool.name for tool in tools.tools}

                response = await client.call_tool("ping", {})
                assert not response.is_error
                assert response.structured_content == {"result": "pong"}

    asyncio.run(check_ping())
