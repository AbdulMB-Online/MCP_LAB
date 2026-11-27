"""Use a Microsoft Foundry v1 Chat Completions deployment for tool selection."""

import os
from typing import Any
from urllib.parse import urlsplit

from httpx2 import AsyncClient
from mcp.types import Tool


class FoundryModel:
    def __init__(
        self,
        http: AsyncClient,
        base_url: str,
        api_key: str,
        deployment: str,
        *,
        tool_reasoning_effort: str = "",
    ) -> None:
        url = urlsplit(base_url)
        if url.scheme != "https" or not url.hostname or url.query or url.fragment:
            raise ValueError("FOUNDRY_BASE_URL must be an HTTPS Foundry v1 base URL.")
        if not url.path.rstrip("/").endswith("/openai/v1"):
            raise ValueError("FOUNDRY_BASE_URL must end with /openai/v1.")
        self._http = http
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._deployment = deployment
        self._tool_reasoning_effort = tool_reasoning_effort

    @classmethod
    def from_environment(cls, http: AsyncClient) -> "FoundryModel":
        keys = ("FOUNDRY_BASE_URL", "FOUNDRY_API_KEY", "FOUNDRY_MODEL")
        settings = {key: os.environ.get(key, "").strip() for key in keys}
        missing = [key for key, value in settings.items() if not value]
        if missing:
            raise ValueError(
                f"Set Foundry environment variables: {', '.join(missing)}."
            )
        return cls(
            http,
            *(settings[key] for key in keys),
            tool_reasoning_effort=os.environ.get(
                "FOUNDRY_REASONING_EFFORT", ""
            ).strip(),
        )

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[Tool]
    ) -> dict[str, Any]:
        """Send the conversation and discovered MCP tool schemas to Foundry."""
        payload: dict[str, Any] = {"model": self._deployment, "messages": messages}
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
            payload["parallel_tool_calls"] = False
            if self._tool_reasoning_effort:
                payload["reasoning_effort"] = self._tool_reasoning_effort
        response = await self._http.post(
            self._url, json=payload, headers={"api-key": self._api_key}
        )
        response.raise_for_status()
        try:
            choice = response.json()["choices"][0]
            finish_reason = choice["finish_reason"]
            message = choice["message"]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError("Foundry did not return an assistant turn.") from error
        if finish_reason not in ("stop", "tool_calls"):
            raise ValueError(f"Foundry ended the turn with {finish_reason!r}.")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("Foundry did not return an assistant turn.")
        return message
