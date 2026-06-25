# agents/retriever.py
# Retriever Agent — embeds the user query and fetches relevant chunks
# from the VectorDB server (POST /search).

from __future__ import annotations
import asyncio
import json
import os
from typing import Any

import boto3
import urllib.parse
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
# VectorDB client helper
# ---------------------------------------------------------------------------

async def _search(query: str, top_k: int) -> dict:
    """Call Retrieve on Bedrock Knowledge Bases."""
    def _do_retrieve():
        kb_id = os.getenv("KNOWLEDGE_BASE_ID")
        if not kb_id:
            raise ValueError("KNOWLEDGE_BASE_ID environment variable is not set.")
        bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
        return bedrock_agent.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
        )
    
    response = await asyncio.to_thread(_do_retrieve)
    
    results = [
        {
            "chunk_id":    urllib.parse.unquote(r["metadata"].get("x-amz-bedrock-kb-chunk-id", "")),
            "document_id": r["location"]["s3Location"]["uri"],
            "text":        r["content"]["text"],
            "score":       r["score"],
            "metadata":    r["metadata"],
        }
        for r in response.get("retrievalResults", [])
    ]
    return {"results": results}


# ---------------------------------------------------------------------------
# Retriever agent
# ---------------------------------------------------------------------------

class RetrieverAgent:
    """
    Step 1 of the pipeline.

    Responsibilities:
      - Call POST /search on the VectorDB server
      - Retrieve the top-k most similar document chunks
      - Return structured RetrievedChunk objects for the Reasoner
    """

    def __init__(self, top_k: int = 5):
        self.top_k = top_k

    async def run(self, query: str) -> RetrieverOutput:
        print(f"[RetrieverAgent] Retrieving top-{self.top_k} chunks for: {query!r}")

        raw = await _search(query=query, top_k=self.top_k)

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
