"""End-to-end smoke test against the running Docker container."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from mcp import Client


BASE_URL = "http://127.0.0.1:8080"
EXPECTED_VERSION = {
    "service": "BIRZHA MCP FORECAST",
    "version": "0.1.0",
    "architecture": "BIRZHA_MCP_FORECAST_V0_2",
}


def check_health() -> None:
    with urllib.request.urlopen(f"{BASE_URL}/healthz", timeout=5) as response:
        payload = json.load(response)
    assert payload["status"] == "ok", payload
    assert payload["service"] == EXPECTED_VERSION["service"], payload
    assert payload["version"] == EXPECTED_VERSION["version"], payload


def check_rebinding_protection() -> None:
    request = urllib.request.Request(
        f"{BASE_URL}/mcp",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Host": "untrusted.example",
        },
    )
    try:
        urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        assert exc.code == 421, exc
    else:
        raise AssertionError("MCP transport accepted an untrusted Host header")


async def check_mcp() -> None:
    async with Client(f"{BASE_URL}/mcp") as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools.tools}
        assert "system.version" in tool_names, tool_names

        result = await client.call_tool("system.version", {})
        assert not result.is_error, result
        assert result.structured_content == EXPECTED_VERSION, result


def main() -> None:
    check_health()
    check_rebinding_protection()
    asyncio.run(check_mcp())
    print("DOCKER_SMOKE_PASS")


if __name__ == "__main__":
    main()
