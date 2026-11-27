"""Verify the Foundry request and response contract without network access."""

import asyncio
import json

import pytest
from httpx2 import (
    AsyncClient,
    HTTPStatusError,
    MockTransport,
    ReadTimeout,
    Request,
    Response,
)
from mcp.types import Tool

from mcp_lab.foundry import FoundryModel

BASE_URL = "https://example.openai.azure.com/openai/v1/"
MESSAGES = [{"role": "user", "content": "Find EMP-001"}]
EMPLOYEE_SCHEMA = {"type": "object", "properties": {"employee_id": {"type": "string"}}}
TOOL_TURN = {
    "role": "assistant",
    "tool_calls": [
        {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "get_employee_info",
                "arguments": '{"employee_id": "EMP-001"}',
            },
        }
    ],
}


@pytest.fixture
def completion_exchange() -> tuple[Request, dict]:
    tool = Tool(
        name="get_employee_info",
        description="Read employee data.",
        inputSchema=EMPLOYEE_SCHEMA,
    )
    requests: list[Request] = []

    def respond(request: Request) -> Response:
        requests.append(request)
        return Response(
            200,
            json={"choices": [{"finish_reason": "tool_calls", "message": TOOL_TURN}]},
        )

    async def complete() -> dict:
        async with AsyncClient(transport=MockTransport(respond)) as http:
            model = FoundryModel(http, BASE_URL, "example-key", "test-deployment")
            return await model.complete(MESSAGES, [tool])

    result = asyncio.run(complete())
    (request,) = requests
    return request, result


def test_foundry_sends_discovered_tool_schema(
    completion_exchange: tuple[Request, dict],
) -> None:
    request, _ = completion_exchange
    assert json.loads(request.content) == {
        "model": "test-deployment",
        "messages": MESSAGES,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_employee_info",
                    "description": "Read employee data.",
                    "parameters": EMPLOYEE_SCHEMA,
                },
            }
        ],
        "parallel_tool_calls": False,
    }


def test_foundry_posts_to_deployment_endpoint(
    completion_exchange: tuple[Request, dict],
) -> None:
    request, _ = completion_exchange
    assert (request.method, str(request.url)) == ("POST", f"{BASE_URL}chat/completions")


def test_foundry_passes_key_as_header(
    completion_exchange: tuple[Request, dict],
) -> None:
    request, _ = completion_exchange
    assert request.headers["api-key"] == "example-key"


def test_foundry_returns_assistant_tool_turn(
    completion_exchange: tuple[Request, dict],
) -> None:
    _, result = completion_exchange
    assert result == TOOL_TURN


def test_foundry_rejects_truncated_assistant_turn() -> None:
    # Arrange
    def respond(_: Request) -> Response:
        return Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "length", "message": {"role": "assistant"}}
                ]
            },
        )

    async def complete() -> None:
        async with AsyncClient(transport=MockTransport(respond)) as http:
            model = FoundryModel(http, BASE_URL, "example-key", "test-deployment")
            await model.complete([], [])

    # Act / Assert
    with pytest.raises(ValueError, match="length"):
        asyncio.run(complete())


@pytest.mark.parametrize(
    ("url", "message"),
    [("http://example.com/openai/v1/", "HTTPS"), ("https://example.com/", "end with")],
    ids=["insecure-url", "incorrect-base-path"],
)
def test_foundry_rejects_invalid_base_url(url: str, message: str) -> None:
    async def configure() -> None:
        async with AsyncClient() as http:
            FoundryModel(http, url, "example-key", "test-deployment")

    with pytest.raises(ValueError, match=message):
        asyncio.run(configure())


def test_foundry_lists_missing_environment_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_BASE_URL", BASE_URL)
    monkeypatch.delenv("FOUNDRY_API_KEY", raising=False)
    monkeypatch.delenv("FOUNDRY_MODEL", raising=False)

    async def configure() -> None:
        async with AsyncClient() as http:
            FoundryModel.from_environment(http)

    with pytest.raises(ValueError, match="FOUNDRY_API_KEY, FOUNDRY_MODEL"):
        asyncio.run(configure())


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"choices": []}, "did not return an assistant turn"),
        (
            {"choices": [{"finish_reason": "stop", "message": {"role": "user"}}]},
            "did not return an assistant turn",
        ),
    ],
    ids=["empty-choices", "wrong-role"],
)
def test_foundry_rejects_invalid_assistant_turn(payload: dict, message: str) -> None:
    def respond(_: Request) -> Response:
        return Response(200, json=payload)

    async def complete() -> None:
        async with AsyncClient(transport=MockTransport(respond)) as http:
            model = FoundryModel(http, BASE_URL, "example-key", "test-deployment")
            await model.complete([], [])

    with pytest.raises(ValueError, match=message):
        asyncio.run(complete())


def test_foundry_loads_configuration_and_completes_without_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_BASE_URL", BASE_URL)
    monkeypatch.setenv("FOUNDRY_API_KEY", "example-key")
    monkeypatch.setenv("FOUNDRY_MODEL", "test-deployment")
    monkeypatch.setenv("FOUNDRY_REASONING_EFFORT", "none")
    bodies = []

    def respond(request: Request) -> Response:
        bodies.append(json.loads(request.content))
        return Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Done."},
                    }
                ]
            },
        )

    async def complete() -> None:
        async with AsyncClient(transport=MockTransport(respond)) as http:
            model = FoundryModel.from_environment(http)
            await model.complete([], [])

    asyncio.run(complete())

    assert bodies == [{"model": "test-deployment", "messages": []}]


def test_foundry_sends_configured_reasoning_effort_with_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOUNDRY_BASE_URL", BASE_URL)
    monkeypatch.setenv("FOUNDRY_API_KEY", "example-key")
    monkeypatch.setenv("FOUNDRY_MODEL", "test-deployment")
    monkeypatch.setenv("FOUNDRY_REASONING_EFFORT", "none")
    tool = Tool(name="get_employee_info", inputSchema=EMPLOYEE_SCHEMA)
    bodies = []

    def respond(request: Request) -> Response:
        bodies.append(json.loads(request.content))
        return Response(
            200,
            json={"choices": [{"finish_reason": "tool_calls", "message": TOOL_TURN}]},
        )

    async def complete() -> None:
        async with AsyncClient(transport=MockTransport(respond)) as http:
            await FoundryModel.from_environment(http).complete(MESSAGES, [tool])

    asyncio.run(complete())

    assert bodies[0].get("reasoning_effort") == "none"


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_foundry_preserves_http_status_on_error(status: int) -> None:
    def respond(_: Request) -> Response:
        return Response(
            status,
            headers={"Content-Type": "text/html"},
            text="<h1>Upstream error</h1>",
        )

    async def complete() -> None:
        async with AsyncClient(transport=MockTransport(respond)) as http:
            model = FoundryModel(http, BASE_URL, "example-key", "test-deployment")
            await model.complete([], [])

    with pytest.raises(HTTPStatusError) as error:
        asyncio.run(complete())

    assert error.value.response.status_code == status


def test_foundry_preserves_transport_read_timeout() -> None:
    def respond(request: Request) -> Response:
        raise ReadTimeout("Foundry timed out", request=request)

    async def complete() -> None:
        async with AsyncClient(transport=MockTransport(respond)) as http:
            model = FoundryModel(http, BASE_URL, "example-key", "test-deployment")
            await model.complete([], [])

    with pytest.raises(ReadTimeout, match="Foundry timed out"):
        asyncio.run(complete())


def test_foundry_rejects_non_json_success_response() -> None:
    def respond(_: Request) -> Response:
        return Response(200, text="<h1>Unexpected proxy response</h1>")

    async def complete() -> None:
        async with AsyncClient(transport=MockTransport(respond)) as http:
            model = FoundryModel(http, BASE_URL, "example-key", "test-deployment")
            await model.complete([], [])

    with pytest.raises(ValueError, match="did not return an assistant turn"):
        asyncio.run(complete())
