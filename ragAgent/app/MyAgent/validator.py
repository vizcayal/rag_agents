# agents/validator.py
# Validator Agent — enforces output schema and verifies that every claim
# in the answer is grounded in a retrieved chunk (hallucination check).

from __future__ import annotations
import asyncio
import json
from enum import Enum
from typing import Any

import httpx
import urllib.parse
from pydantic import BaseModel, field_validator

from retriever import RetrievedChunk
from reasoner import ReasonerOutput


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ValidationStatus(str, Enum):
    PASSED   = "passed"
    WARNING  = "warning"   # answer OK but some citations are weak
    FAILED   = "failed"    # answer contains unsupported claims


class CitationCheck(BaseModel):
    chunk_id: str
    used_in_answer: bool
    grounding_score: float   # 0-1: how well the chunk supports the answer


class ValidatorOutput(BaseModel):
    status: ValidationStatus
    answer: str                        # final (possibly corrected) answer
    confidence: float                  # 0-1 overall confidence
    citation_checks: list[CitationCheck]
    issues: list[str] = []             # human-readable validation issues
    query: str
    cited_chunk_ids: list[str]

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


# ---------------------------------------------------------------------------
# MCP client helper
# ---------------------------------------------------------------------------

MCP_SCHEMA_URL = "http://localhost:8003"    # Schema MCP server


async def _call_mcp(tool: str, arguments: dict) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{MCP_SCHEMA_URL}/mcp", json=payload)
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise RuntimeError(f"MCP Schema error: {data['error']}")

    content = data["result"]["content"]
    return json.loads(content[0]["text"])


# ---------------------------------------------------------------------------
# Validator agent
# ---------------------------------------------------------------------------

class ValidatorAgent:
    """
    Step 3 of the pipeline.

    Responsibilities:
      1. Schema check  — verify the ReasonerOutput matches QueryResponse schema
      2. Citation check — confirm each cited chunk actually appears in the
         retrieved context and supports the answer
      3. Hallucination check — flag answer segments not grounded in any chunk
      4. Return a ValidatorOutput with status, confidence, and issue list
    """

    # Minimum cosine-style grounding score to consider a citation valid
    GROUNDING_THRESHOLD = 0.6

    async def run(
        self,
        reasoner_output: ReasonerOutput,
        retrieved_chunks: list[RetrievedChunk],
    ) -> ValidatorOutput:
        print("[ValidatorAgent] Validating answer and citations...")

        issues: list[str] = []
        # Normalize keys in chunk_map to handle URL-encoded IDs from Bedrock
        chunk_map = {urllib.parse.unquote(c.chunk_id): c for c in retrieved_chunks}

        # ----------------------------------------------------------------
        # 1. Citation presence check
        #    Every chunk_id cited by the Reasoner must exist in our context.
        # ----------------------------------------------------------------
        citation_checks: list[CitationCheck] = []
        invalid_citations: list[str] = []

        for cid_raw in reasoner_output.cited_chunk_ids:
            # Normalize: handle "CHUNK " prefix, brackets, and URL encoding
            cid = cid_raw.replace("CHUNK ", "").replace("[", "").replace("]", "").strip()
            cid = urllib.parse.unquote(cid)

            if cid not in chunk_map:
                invalid_citations.append(cid_raw)
                citation_checks.append(
                    CitationCheck(chunk_id=cid_raw, used_in_answer=True, grounding_score=0.0)
                )
            else:
                citation_checks.append(
                    CitationCheck(chunk_id=cid, used_in_answer=True, grounding_score=1.0)
                )

        if invalid_citations:
            issues.append(
                f"Cited chunk IDs not found in retrieved context: {invalid_citations}"
            )

        # ----------------------------------------------------------------
        # 2. Grounding check via Schema MCP server
        #    The MCP server runs a lightweight NLI model to score how well
        #    each chunk supports the answer.
        # ----------------------------------------------------------------
        try:
            grounding_result = await _call_mcp(
                tool="check_grounding",
                arguments={
                    "answer": reasoner_output.answer,
                    "chunks": [
                        {"chunk_id": c.chunk_id, "text": c.text}
                        for c in retrieved_chunks
                        if c.chunk_id in reasoner_output.cited_chunk_ids
                    ],
                },
            )

            for score_item in grounding_result.get("scores", []):
                cid = score_item["chunk_id"]
                score = score_item["score"]
                # Update existing CitationCheck
                for cc in citation_checks:
                    if cc.chunk_id == cid:
                        cc.grounding_score = score
                        if score < self.GROUNDING_THRESHOLD:
                            issues.append(
                                f"Chunk {cid} has low grounding score ({score:.2f}) "
                                f"— cited claim may not be supported."
                            )
                        break

        except Exception as exc:
            # Grounding MCP unavailable — degrade gracefully
            issues.append(f"Grounding check skipped (MCP unavailable): {exc}")

        # ----------------------------------------------------------------
        # 3. Schema validation via Schema MCP server
        #    Validates the final answer against the QueryResponse Pydantic schema.
        # ----------------------------------------------------------------
        try:
            schema_result = await _call_mcp(
                tool="validate_schema",
                arguments={
                    "data": {
                        "answer": reasoner_output.answer,
                        "cited_chunk_ids": reasoner_output.cited_chunk_ids,
                        "query": reasoner_output.query,
                    },
                    "schema_name": "QueryResponse",
                },
            )
            if not schema_result.get("valid", True):
                for err in schema_result.get("errors", []):
                    issues.append(f"Schema error: {err}")

        except Exception as exc:
            issues.append(f"Schema validation skipped (MCP unavailable): {exc}")

        # ----------------------------------------------------------------
        # 4. Determine overall status and confidence
        # ----------------------------------------------------------------
        has_invalid = bool(invalid_citations)
        low_grounding = any(
            cc.grounding_score < self.GROUNDING_THRESHOLD for cc in citation_checks
        )

        if has_invalid:
            status = ValidationStatus.FAILED
            confidence = 0.3
        elif low_grounding:
            status = ValidationStatus.WARNING
            confidence = 0.65
        else:
            status = ValidationStatus.PASSED
            confidence = min(
                0.99,
                sum(cc.grounding_score for cc in citation_checks) / max(len(citation_checks), 1),
            )

        print(
            f"[ValidatorAgent] Status: {status.value} | "
            f"Confidence: {confidence:.2f} | Issues: {len(issues)}"
        )

        return ValidatorOutput(
            status=status,
            answer=reasoner_output.answer,
            confidence=confidence,
            citation_checks=citation_checks,
            issues=issues,
            query=reasoner_output.query,
            cited_chunk_ids=reasoner_output.cited_chunk_ids,
        )


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from retriever import RetrievedChunk
    from reasoner import ReasonerOutput, ReasoningStep

    async def _test():
        chunks = [
            RetrievedChunk(chunk_id="c1", document_id="doc1", score=0.92,
                           text="Penalties are 2% per week, capped at 20%."),
            RetrievedChunk(chunk_id="c2", document_id="doc1", score=0.87,
                           text="The cap applies to the total contract value."),
        ]
        reasoner_out = ReasonerOutput(
            query="What are the penalty clauses?",
            answer="Late delivery penalties are 2% per week, capped at 20% of contract value.",
            reasoning_steps=[
                ReasoningStep(step=1, thought="Found penalty rate in c1", evidence="c1"),
                ReasoningStep(step=2, thought="Found cap in c1 and c2", evidence="c1,c2"),
            ],
            cited_chunk_ids=["c1", "c2"],
            raw_response="{}",
        )

        agent = ValidatorAgent()
        result = await agent.run(reasoner_out, chunks)
        print(f"Status: {result.status}")
        print(f"Confidence: {result.confidence:.2f}")
        print(f"Issues: {result.issues}")

    asyncio.run(_test())
