# agents/retriever.py
# Retriever Agent — embeds the user query and fetches relevant chunks
# from the vector DB through the VectorDB MCP server.

from __future__ import annotations
import asyncio
import json
from typing import Any

import httpx
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    score: float          # cosine similarity score from the vector DB
    metadata: dict[str, Any] = {}


class RetrieverOutput(BaseModel):
    query: str
    chunks: list[RetrievedChunk]


# ---------------------------------------------------------------------------
# MCP client helper
# ---------------------------------------------------------------------------

MCP_VECTORDB_URL = "http://localhost:8001"   # VectorDB MCP server


async def _call_mcp(tool: str, arguments: dict) -> dict:
    """Send a tool-call request to the VectorDB MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{MCP_VECTORDB_URL}/mcp", json=payload)
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise RuntimeError(f"MCP VectorDB error: {data['error']}")

    # MCP returns content as a list of {type, text} objects
    content = data["result"]["content"]
    return json.loads(content[0]["text"])


# ---------------------------------------------------------------------------
# Retriever agent
# ---------------------------------------------------------------------------

class RetrieverAgent:
    """
    Step 1 of the pipeline.

    Responsibilities:
      - Ask the VectorDB MCP server to embed the query
      - Retrieve the top-k most similar document chunks
      - Return structured RetrievedChunk objects for the Reasoner
    """

    def __init__(self, top_k: int = 5):
        self.top_k = top_k

    async def run(self, query: str) -> RetrieverOutput:
        print(f"[RetrieverAgent] Retrieving top-{self.top_k} chunks for: {query!r}")

        # 1. Ask MCP server to embed + search
        raw = await _call_mcp(
            tool="search_documents",
            arguments={"query": query, "top_k": self.top_k},
        )

        # 2. Parse results into typed chunks
        chunks = [
            RetrievedChunk(
                chunk_id=item["chunk_id"],
                document_id=item["document_id"],
                text=item["text"],
                score=item["score"],
                metadata=item.get("metadata", {}),
            )
            for item in raw.get("results", [])
        ]

        print(f"[RetrieverAgent] Retrieved {len(chunks)} chunks.")
        return RetrieverOutput(query=query, chunks=chunks)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    async def _test():
        agent = RetrieverAgent(top_k=3)
        output = await agent.run("What are the penalty clauses in the contracts?")
        for c in output.chunks:
            print(f"  [{c.score:.3f}] {c.text[:80]}...")

    asyncio.run(_test())
