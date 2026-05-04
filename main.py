# main.py
# FastAPI Gateway — synchronous RAG pipeline
# Run: uv run uvicorn main:app --reload --port 8000

from __future__ import annotations
import os

import httpx
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from orchestrator import run_pipeline

VECTORDB_URL = os.getenv("VECTORDB_URL", "http://localhost:8001")

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=20)


class QueryResponse(BaseModel):
    status:          str
    answer:          str | None
    confidence:      float
    cited_chunk_ids: list[str]
    issues:          list[str] = []
    query:           str


class IngestRequest(BaseModel):
    document_id: str
    chunks: list[dict] = Field(..., description='[{"text": str, "metadata": dict}]')


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Document Intelligence API",
    description="Multi-agent RAG pipeline powered by LangGraph + ChromaDB",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/query", response_model=QueryResponse, summary="Run RAG query")
async def query(req: QueryRequest) -> QueryResponse:
    """Run the full Retriever → Reasoner → Validator pipeline."""
    result = await run_pipeline(query=req.query, top_k=req.top_k)

    if result["status"] == "failed":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("error", "Pipeline failed."),
        )

    return QueryResponse(**result)


@app.post("/ingest", status_code=status.HTTP_201_CREATED, summary="Ingest document chunks")
async def ingest(req: IngestRequest) -> dict:
    """Forward document chunks to the VectorDB server."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{VECTORDB_URL}/ingest",
            json={"document_id": req.document_id, "chunks": req.chunks},
        )
        resp.raise_for_status()
    return resp.json()


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Run: uv run uvicorn main:app --reload --port 8000
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
