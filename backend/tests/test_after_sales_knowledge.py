"""Tests for the AfterFlow MCP-client RAG knowledge retrieval (inform layer).

The MCP client is real, but tests inject a fake client so no actual RAG server
is needed: hits pass through, an unavailable/empty server falls back to the
deterministic policy knowledge source.
"""

import json

import pytest

from app.after_sales.knowledge import (
    McpKnowledgeClient,
    _mock_knowledge,
    retrieve_knowledge,
    search_after_sales_knowledge_tool,
)


class FakeKnowledgeClient:
    def __init__(self, hits, error=None):
        self._hits = hits
        self._error = error

    async def search(self, query: str):
        if self._error is not None:
            raise self._error
        return self._hits


@pytest.mark.asyncio
async def test_mcp_client_hits_pass_through():
    hits = [{"doc": "policy", "snippet": "破损规则", "citation": "AFTER-SALES-CN@2026.07"}]

    result = await retrieve_knowledge("破损", client=FakeKnowledgeClient(hits))

    assert result == hits


@pytest.mark.asyncio
async def test_unavailable_mcp_server_falls_back_to_mock():
    result = await retrieve_knowledge(
        "破损",
        client=FakeKnowledgeClient([], error=RuntimeError("mcp server down")),
    )

    assert result and result[0]["citation"].startswith("AFTER-SALES-CN@")
    assert "覆盖问题" in result[0]["snippet"]


@pytest.mark.asyncio
async def test_empty_hits_fall_back_to_mock():
    result = await retrieve_knowledge("破损", client=FakeKnowledgeClient([]))

    assert result == _mock_knowledge("破损")


class _Fake:
    def __init__(self, search):
        self._search = search

    async def search(self, query):
        return await self._search(query)


@pytest.mark.asyncio
async def test_knowledge_tool_returns_json_list(monkeypatch):
    async def _fake_search(query):
        return [{"doc": "policy", "snippet": "s", "citation": "AFTER-SALES-CN@2026.07"}]

    monkeypatch.setattr("app.after_sales.knowledge.get_default_knowledge_client", lambda: _Fake(_fake_search))
    raw = await search_after_sales_knowledge_tool.ainvoke({"query": "破损"})

    parsed = json.loads(raw)
    assert isinstance(parsed, list)
    assert parsed[0]["citation"].startswith("AFTER-SALES-CN@")


@pytest.mark.asyncio
async def test_tool_falls_back_when_no_server(monkeypatch):
    async def _broken(query):
        raise ConnectionError("no server")

    monkeypatch.setattr("app.after_sales.knowledge.get_default_knowledge_client", lambda: _Fake(_broken))
    raw = await search_after_sales_knowledge_tool.ainvoke({"query": "破损"})

    parsed = json.loads(raw)
    assert parsed[0]["citation"].startswith("AFTER-SALES-CN@")


def test_mcp_client_requires_url_for_sse_transport():
    with pytest.raises(ValueError, match="url"):
        McpKnowledgeClient(transport="sse")
