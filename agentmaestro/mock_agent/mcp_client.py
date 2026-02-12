"""Async MCP HTTP client for calling scheduler coordination tools."""

import uuid

import httpx


class McpClient:
    """Minimal MCP client that calls tools on the scheduler's MCP endpoint."""

    def __init__(self, url: str, headers: dict[str, str]):
        self.url = url
        self.headers = headers
        self._client = httpx.AsyncClient()

    async def call_tool(self, name: str, arguments: dict) -> dict:
        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        resp = await self._client.post(self.url, json=body, headers=self.headers)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data.get("result", {})

    async def close(self):
        await self._client.aclose()
