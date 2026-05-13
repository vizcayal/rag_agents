# agents/reasoner.py
# Reasoner Agent — sends retrieved chunks + user query directly to Claude
# using the Anthropic SDK (no intermediate MCP server needed).

from __future__ import annotations
import asyncio
import json
import os
import re
from typing import Any

import boto3
from pydantic import BaseModel

from retriever import RetrievedChunk


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ReasoningStep(BaseModel):
    step: int
    thought: str    # intermediate reasoning step
    evidence: Any   # which chunk(s) support this step (model may return string or list)


class ReasonerOutput(BaseModel):
    query:           str
    answer:          str
    reasoning_steps: list[ReasoningStep]
    cited_chunk_ids: list[str]  # chunk IDs actually used in the answer
    raw_response:    str        # full Claude response for audit trail


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(query: str, chunks: list[RetrievedChunk]) -> str:
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
2. For each reasoning step, cite the EXACT CHUNK ID (e.g. 1:0:ABC) that supports it.
3. IMPORTANT: Do NOT add "CHUNK" prefix or brackets to the ID. Use the ID exactly as provided.
4. Identify which chunks you actually used in your final answer.
5. Return your response as a JSON object with this exact schema:

{{
  "answer": "<your final answer, clear and concise>",
  "reasoning_steps": [
    {{
      "step": 1,
      "thought": "<what you're reasoning about>",
      "evidence": "<exact_chunk_id>"
    }}
  ],
  "cited_chunk_ids": ["<exact_chunk_id_1>", "<exact_chunk_id_2>"]
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
      - Call Claude directly via the Anthropic SDK
      - Parse and return structured reasoning output
    """

    def __init__(
        self,
        model: str = "us.amazon.nova-lite-v1:0",
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = None  # lazy init to avoid initialization timeout

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
        return self._client

    async def run(self, query: str, chunks: list[RetrievedChunk]) -> ReasonerOutput:
        print(f"[ReasonerAgent] Reasoning over {len(chunks)} chunks...")

        prompt = _build_prompt(query, chunks)

        # Call Claude via Bedrock (using to_thread since boto3 is synchronous)
        response = await asyncio.to_thread(
            self.client.converse,
            modelId=self.model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={
                "maxTokens": self.max_tokens,
                "temperature": self.temperature,
            }
        )

        raw_text: str = response["output"]["message"]["content"][0]["text"]

        # Parse the structured JSON response from Claude
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            # Fallback: Claude sometimes wraps JSON in markdown fences
            match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
            else:
                raise ValueError(f"Could not parse Claude response as JSON:\n{raw_text}")

        output = ReasonerOutput(
            query=query,
            answer=parsed["answer"],
            reasoning_steps=[ReasoningStep(**s) for s in parsed.get("reasoning_steps", [])],
            cited_chunk_ids=parsed.get("cited_chunk_ids", []),
            raw_response=raw_text,
        )

        print(f"[ReasonerAgent] Answer generated. Used {len(output.cited_chunk_ids)} chunks.")
        return output


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
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
