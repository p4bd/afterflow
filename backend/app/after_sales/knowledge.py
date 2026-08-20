"""RAG knowledge retrieval via an MCP client — the INFORM layer.

AfterFlow uses an MCP client to call external knowledge services (e.g. the
RAG+MCP knowledge base project) for policy and customer-facing explanations.
This is strictly the INFORM layer: retrieved text backs citations and answers
to "why is this refund handled this way". The money decision stays in the
deterministic engine (`decide_resolution` / `PolicySnapshot`) — the VALIDATE
layer. The two never mix: RAG informs, the engine validates.

The MCP server may be unavailable in a local demo; retrieval fails open to a
deterministic mock knowledge source rather than breaking the case workflow.
"""

import json
from typing import Any, Protocol

from langchain.tools import tool

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
            "snippet": (
                f"政策版本 {policy.get('version', '?')}，覆盖问题："
                f"{', '.join(policy.get('covered_issues', []))}；"
                f"高价值线 {policy.get('high_value_amount', 0)} 分，人工审核线 {policy.get('manual_review_amount', 0)} 分。"
            ),
            "citation": f"{policy.get('policy_id', 'AFTER-SALES-CN')}@{policy.get('version', '?')}",
        }
    ]


async def retrieve_knowledge(query: str, client: KnowledgeClient | None = None) -> list[dict]:
    """Retrieve knowledge via the MCP client, falling back to the mock source.

    Retrieval is fail-open (inform layer): an unavailable or empty RAG server
    yields deterministic policy knowledge, never a broken workflow. The
    deterministic engine still decides eligibility/amount regardless.
    """
    client = client or get_default_knowledge_client()
    try:
        hits = await client.search(query)
        return hits or _mock_knowledge(query)
    except Exception:
        return _mock_knowledge(query)


@tool("search_after_sales_knowledge")
async def search_after_sales_knowledge_tool(query: str) -> str:
    """Search the after-sales RAG knowledge base (via MCP) for policy text and citations."""
    hits = await retrieve_knowledge(query)
    return json.dumps(hits, ensure_ascii=False)
