"""Shared Streamable HTTP clients and subprocess lifecycle for integration tests."""

import os
import re
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import pytest
from httpx2 import ASGITransport, AsyncClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer

_STARTUP_URL = re.compile(r"Uvicorn running on (http://127\.0\.0\.1:\d+)")


@pytest.fixture
def mcp_http_client():
    @asynccontextmanager
    async def connect(server: MCPServer) -> AsyncIterator[Client]:
        asgi = server.streamable_http_app(json_response=True, stateless_http=True)
        async with asgi.router.lifespan_context(asgi):
            async with AsyncClient(transport=ASGITransport(app=asgi)) as http:
                async with Client(
                    streamable_http_client(
                        "http://127.0.0.1:8001/mcp", http_client=http
                    ),
                    read_timeout_seconds=5,
                ) as client:
                    yield client

    return connect


@pytest.fixture
def serve_module(tmp_path: Path):
    @contextmanager
    def serve(module: str, **settings: str) -> Iterator[str]:
        log_path = tmp_path / "server.log"
        environment = {
            **os.environ,
            **settings,
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": "0",
        }
        with log_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                [sys.executable, "-u", "-m", module],
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 10
                while process.poll() is None and time.monotonic() < deadline:
                    if match := _STARTUP_URL.search(
                        log_path.read_text(encoding="utf-8")
                    ):
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail(
                        f"{module} failed to start (exit code {process.poll()}):\n"
                        f"{log_path.read_text(encoding='utf-8')}"
                    )
                yield f"{match[1]}/mcp"
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    return serve
