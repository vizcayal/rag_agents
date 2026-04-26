# agents/reasoner.py
# Reasoner Agent — sends retrieved chunks + user query to Claude via the
# Claude MCP server and extracts a structured chain-of-thought answer.

from __future__ import annotations
import asyncio
import json
from typing import Any

import httpx
from pydantic import BaseModel

from retriever import RetrievedChunk


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ReasoningStep(BaseModel):
    step: int
    thought: str      # intermediate reasoning step
    evidence: str     # which chunk(s) support this step


class ReasonerOutput(BaseModel):
    query: str
    answer: str
    reasoning_steps: list[ReasoningStep]
    cited_chunk_ids: list[str]   # chunk IDs actually used in the answer
    raw_response: str            # full Claude response for audit trail


# ---------------------------------------------------------------------------
# MCP client helper
# ---------------------------------------------------------------------------

MCP_CLAUDE_URL = "http://localhost:8002"    # Claude MCP server


async def _call_mcp(tool: str, arguments: dict) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{MCP_CLAUDE_URL}/mcp", json=payload)
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise RuntimeError(f"MCP Claude error: {data['error']}")

    content = data["result"]["content"]
    return json.loads(content[0]["text"])


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(query: str, chunks: list[RetrievedChunk]) -> str:
    """
    Build a chain-of-thought prompt that instructs Claude to:
      1. Reason step-by-step over the provided document chunks
      2. Cite which chunk IDs support each claim
      3. Return a structured JSON response
    """
    context_block = "\n\n".join(
        f"[CHUNK {c.chunk_id}] (doc: {c.document_id}, score: {c.score:.3f})\n{c.text}"
        for c in chunks
    )

    return f"""You are a document intelligence assistant. Answer the user's question
using ONLY the document chunks provided below. Think step by step.

## Document Chunks
{context_block}

## User Question
{query}

## Instructions
1. Reason through the question step by step.
2. For each reasoning step, cite the specific CHUNK ID(s) that support it.
3. Identify which chunks you actually used in your final answer.
4. Return your response as a JSON object with this exact schema:

{{
  "answer": "<your final answer, clear and concise>",
  "reasoning_steps": [
    {{
      "step": 1,
      "thought": "<what you're reasoning about>",
      "evidence": "<chunk ID(s) that support this>"
    }}
  ],
  "cited_chunk_ids": ["<chunk_id_1>", "<chunk_id_2>"]
}}

Return ONLY the JSON object, no markdown fences or preamble."""


# ---------------------------------------------------------------------------
# Reasoner agent
# ---------------------------------------------------------------------------

class ReasonerAgent:
    """
    Step 2 of the pipeline.

    Responsibilities:
      - Build a grounded, chain-of-thought prompt from retrieved chunks
      - Call Claude via the Claude MCP server
      - Parse and return structured reasoning output
    """

    def __init__(self, model: str = "claude-sonnet-4-5"):
        self.model = model

    async def run(self, query: str, chunks: list[RetrievedChunk]) -> ReasonerOutput:
        print(f"[ReasonerAgent] Reasoning over {len(chunks)} chunks...")

        prompt = _build_prompt(query, chunks)

        # Call Claude through MCP
        raw = await _call_mcp(
            tool="complete",
            arguments={
                "model": self.model,
                "prompt": prompt,
                "max_tokens": 2048,
                "temperature": 0.2,   # low temperature for factual grounding
            },
        )

        raw_text: str = raw.get("text", "")

        # Parse the structured JSON response from Claude
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            # Fallback: Claude sometimes wraps JSON in markdown fences
            import re
            match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
            else:
                raise ValueError(f"Could not parse Claude response as JSON:\n{raw_text}")

        reasoning_steps = [
            ReasoningStep(**step) for step in parsed.get("reasoning_steps", [])
        ]

        output = ReasonerOutput(
            query=query,
            answer=parsed["answer"],
            reasoning_steps=reasoning_steps,
            cited_chunk_ids=parsed.get("cited_chunk_ids", []),
            raw_response=raw_text,
        )

        print(f"[ReasonerAgent] Answer generated. Used {len(output.cited_chunk_ids)} chunks.")
        return output


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from retriever import RetrievedChunk

    async def _test():
        dummy_chunks = [
            RetrievedChunk(
                chunk_id="c1", document_id="doc1", score=0.92,
                text="Section 5.2: Penalties for late delivery are 2% of contract value per week.",
            ),
            RetrievedChunk(
                chunk_id="c2", document_id="doc1", score=0.87,
                text="Section 5.3: Maximum penalty is capped at 20% of total contract value.",
            ),
        ]
        agent = ReasonerAgent()
        output = await agent.run("What are the penalty clauses?", dummy_chunks)
        print(output.answer)
        for step in output.reasoning_steps:
            print(f"  Step {step.step}: {step.thought} [{step.evidence}]")

    asyncio.run(_test())
