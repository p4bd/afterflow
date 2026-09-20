"""RAG knowledge retrieval via an MCP client — the INFORM layer.

AfterFlow uses an MCP client to call external knowledge services (e.g. the
RAG+MCP knowledge base project) for policy and customer-facing explanations.
This is strictly the INFORM layer: retrieved text backs citations and answers
to "why is this refund handled this way". The money decision stays in the
deterministic engine (`decide_resolution` / `PolicySnapshot`) — the VALIDATE
layer. The two never mix: RAG informs, the engine validates.

The MCP server may be unavailable in a local demo; retrieval fails open to a
deterministic mock knowledge source rather than breaking the case workflow.
The fallback is now observable: `retrieve_knowledge` records every attempt
through `KnowledgeHealthRecorder` and the operator can inspect the snapshot
via the `get_knowledge_health` tool.
"""

import json
from typing import Any, Protocol

from langchain.tools import tool

from .knowledge_health import (
    InMemoryKnowledgeHealthRecorder,
    KnowledgeHealthRecorder,
    record_attempt,
)
from .mock_data import POLICIES


class KnowledgeClient(Protocol):
    """Anything with an async `search(query) -> list[dict]`."""

    async def search(self, query: str) -> list[dict]: ...


class McpKnowledgeClient:
    """MCP client that calls the external RAG server's knowledge-search tool.

    Transport uses langchain-mcp-adapters' MultiServerMCPClient (stdio or sse),
    connecting to the server registered under `afterflow-rag`. The server is
    expected to expose a tool named `search_knowledge` that returns hits of the
    shape {"doc", "snippet", "citation"}.
    """

    SEARCH_TOOL = "search_knowledge"

    def __init__(
        self,
        server_name: str = "afterflow-rag",
        transport: str = "stdio",
        command: str = "uv",
        args: list[str] | None = None,
        url: str | None = None,
    ) -> None:
        self._server_name = server_name
        if transport == "stdio":
            self._params: dict[str, Any] = {
                "transport": "stdio",
                "command": command,
                "args": args or ["run", "rag_mcp_server.py"],
            }
        else:
            if url is None:
                raise ValueError("sse/http transport requires a url")
            self._params = {"transport": transport, "url": url}

    async def search(self, query: str) -> list[dict]:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        async with MultiServerMCPClient({self._server_name: self._params}) as client:
            tools = await client.get_tools()
            search_tool = next((t for t in tools if t.name == self.SEARCH_TOOL), None)
            if search_tool is None:
                raise RuntimeError(f"MCP server {self._server_name!r} has no {self.SEARCH_TOOL!r} tool")
            raw = await search_tool.ainvoke({"query": query})
            return _parse_hits(raw)


_default_client: KnowledgeClient | None = None


def get_default_knowledge_client() -> KnowledgeClient:
    """The process-wide knowledge client (override in tests / demo)."""
    global _default_client
    if _default_client is None:
        _default_client = McpKnowledgeClient()
    return _default_client


# Process-wide health recorder. The MCP layer's "fail-open" behaviour now
# becomes observable: every fallback bumps the counter on this recorder.
#
# The default stays in-memory for tests; production lifespan overrides it
# with ``set_knowledge_health_recorder`` so the counter survives process
# restart (see ``SqlKnowledgeHealthRecorder`` and ADR-005).
_health_recorder: KnowledgeHealthRecorder = InMemoryKnowledgeHealthRecorder()


def get_knowledge_health_recorder() -> KnowledgeHealthRecorder:
    """Process-wide knowledge health recorder (override in tests)."""
    return _health_recorder


def set_knowledge_health_recorder(recorder: KnowledgeHealthRecorder) -> KnowledgeHealthRecorder:
    """Replace the process-wide knowledge health recorder.

    The lifespan installs ``SqlKnowledgeHealthRecorder`` here at startup so
    production deployments persist the health counter across restarts.
    The previous recorder is returned for symmetry with ``get_*`` — tests
    can save/restore it the same way they configure the audit provider.
    """
    global _health_recorder
    previous = _health_recorder
    _health_recorder = recorder
    return previous


def _parse_hits(raw: Any) -> list[dict]:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    if isinstance(raw, list):
        return raw
    # LangChain tool result may wrap content.
    if isinstance(raw, dict):
        content = raw.get("content") or raw.get("output")
        if isinstance(content, list):
            return [item.get("text") for item in content if isinstance(item, dict) and item.get("text")]
        if isinstance(content, str):
            return _parse_hits(content)
    return []


def _mock_knowledge(query: str) -> list[dict]:
    """Deterministic fallback knowledge source (demo / offline).

    Mirrors the structured policy so citations stay real even without the RAG
    server running: the retrieved text is informational only.
    """
    policy = POLICIES.get("CN", {})
    return [
        {
            "doc": "AFTER-SALES-CN 售后政策",
            "snippet": (f"政策版本 {policy.get('version', '?')}，覆盖问题：{', '.join(policy.get('covered_issues', []))}；高价值线 {policy.get('high_value_amount', 0)} 分，人工审核线 {policy.get('manual_review_amount', 0)} 分。"),
            "citation": f"{policy.get('policy_id', 'AFTER-SALES-CN')}@{policy.get('version', '?')}",
        }
    ]


async def retrieve_knowledge(query: str, client: KnowledgeClient | None = None) -> list[dict]:
    """Retrieve knowledge via the MCP client, falling back to the mock source.

    Retrieval is fail-open (inform layer): an unavailable or empty RAG server
    yields deterministic policy knowledge, never a broken workflow. The
    deterministic engine still decides eligibility/amount regardless. Every
    attempt is recorded through the health recorder so an operator can
    detect silent degradation via the `get_knowledge_health` tool.
    """
    client = client or get_default_knowledge_client()
    hits = await record_attempt(
        get_knowledge_health_recorder(),
        query=query,
        fn=lambda: client.search(query),
    )
    if hits:
        return hits
    return _mock_knowledge(query)


@tool("search_after_sales_knowledge")
async def search_after_sales_knowledge_tool(query: str) -> str:
    """Search the after-sales RAG knowledge base (via MCP) for policy text and citations."""
    hits = await retrieve_knowledge(query)
    return json.dumps(hits, ensure_ascii=False)


@tool("get_knowledge_health")
async def get_knowledge_health_tool() -> str:
    """Snapshot of the RAG/MCP knowledge layer's health.

    Returns: {healthy, attempt_count, fallback_count, consecutive_failures,
    last_error, last_attempt_at}. Use this to detect silent degradation: if
    `healthy` is false, the demo has been falling back to the deterministic
    mock for several attempts in a row, and answers may be stale.
    """
    snap = await get_knowledge_health_recorder().snapshot()
    return json.dumps(
        {
            "healthy": snap.healthy,
            "attempt_count": snap.attempt_count,
            "fallback_count": snap.fallback_count,
            "consecutive_failures": snap.consecutive_failures,
            "last_error": snap.last_error,
            "last_attempt_at": snap.last_attempt_at.isoformat() if snap.last_attempt_at else None,
        },
        ensure_ascii=False,
    )
