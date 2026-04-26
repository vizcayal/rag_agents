# main.py
# FastAPI Gateway — three query modes (sync, streaming, async/poll)
# all backed by the LangGraph multi-agent orchestrator.

from __future__ import annotations
import json
import os
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, BackgroundTasks, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from orchestrator import run_pipeline, run_pipeline_stream

# ---------------------------------------------------------------------------
# Redis (for async job state)
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_redis: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    yield
    await _redis.aclose()


# ---------------------------------------------------------------------------
# Auth (JWT bearer — replace verify_token with real logic)
# ---------------------------------------------------------------------------

_security = HTTPBearer()
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")


async def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> str:
    """
    Validate the Bearer token.
    Stub: accepts any non-empty token.  Replace with real JWT verification.
    """
    token = credentials.credentials
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token.")
    # TODO: decode and verify JWT with e.g. python-jose
    return token   # return user_id extracted from JWT in production


# ---------------------------------------------------------------------------
# Request / Response schemas
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


class AsyncJobResponse(BaseModel):
    job_id: str
    status: str   # "pending"


class JobStatusResponse(BaseModel):
    job_id:  str
    status:  str          # "pending" | "done" | "failed" | "not_found"
    result:  dict | None  # populated when status == "done"


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Document Intelligence API",
    description="Multi-agent RAG pipeline powered by Claude + LangGraph + MCP",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Endpoint 1 — Synchronous (waits for full pipeline)
# ---------------------------------------------------------------------------

@app.post(
    "/query",
    response_model=QueryResponse,
    summary="Synchronous query",
    description="Runs the full pipeline and returns the complete answer. May take 10–20 s.",
)
async def query(
    req: QueryRequest,
    user: str = Depends(verify_jwt),
) -> QueryResponse:
    result = await run_pipeline(query=req.query, top_k=req.top_k)

    if result["status"] == "failed":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("error", "Pipeline failed."),
        )

    return QueryResponse(**result)


# ---------------------------------------------------------------------------
# Endpoint 2 — Streaming (SSE, real-time token output)
# ---------------------------------------------------------------------------

@app.post(
    "/query/stream",
    summary="Streaming query (SSE)",
    description="Streams pipeline stage events and the final answer as Server-Sent Events.",
)
async def query_stream(
    req: QueryRequest,
    user: str = Depends(verify_jwt),
) -> StreamingResponse:
    async def event_generator():
        try:
            async for chunk in run_pipeline_stream(query=req.query, top_k=req.top_k):
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'event': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Endpoint 3a — Async submit (returns job_id immediately)
# ---------------------------------------------------------------------------

async def _run_and_store(job_id: str, req: QueryRequest) -> None:
    """Background task: run pipeline and persist result in Redis."""
    try:
        result = await run_pipeline(query=req.query, top_k=req.top_k)
        await _redis.set(
            f"job:{job_id}",
            json.dumps({"status": "done", "result": result}),
            ex=3600,   # expire after 1 hour
        )
    except Exception as exc:
        await _redis.set(
            f"job:{job_id}",
            json.dumps({"status": "failed", "result": {"error": str(exc)}}),
            ex=3600,
        )


@app.post(
    "/query/async",
    response_model=AsyncJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Async query (fire and forget)",
    description="Returns a job_id immediately. Poll /query/{job_id}/status for the result.",
)
async def query_async(
    req: QueryRequest,
    bg: BackgroundTasks,
    user: str = Depends(verify_jwt),
) -> AsyncJobResponse:
    job_id = str(uuid.uuid4())
    await _redis.set(f"job:{job_id}", json.dumps({"status": "pending", "result": None}), ex=3600)
    bg.add_task(_run_and_store, job_id, req)
    return AsyncJobResponse(job_id=job_id, status="pending")


# ---------------------------------------------------------------------------
# Endpoint 3b — Async poll (check job status)
# ---------------------------------------------------------------------------

@app.get(
    "/query/{job_id}/status",
    response_model=JobStatusResponse,
    summary="Poll async job status",
)
async def job_status(
    job_id: str,
    user: str = Depends(verify_jwt),
) -> JobStatusResponse:
    raw = await _redis.get(f"job:{job_id}")
    if raw is None:
        return JobStatusResponse(job_id=job_id, status="not_found", result=None)

    data = json.loads(raw)
    return JobStatusResponse(
        job_id=job_id,
        status=data["status"],
        result=data.get("result"),
    )


# ---------------------------------------------------------------------------
# Ingest endpoint — add documents to the vector store
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    document_id: str
    chunks: list[dict] = Field(..., description='[{"text": str, "metadata": dict}]')


@app.post("/ingest", status_code=status.HTTP_201_CREATED, summary="Ingest documents")
async def ingest(req: IngestRequest, user: str = Depends(verify_jwt)) -> dict:
    """
    Forward ingestion to the VectorDB MCP server.
    Separate endpoint so ingestion and querying are decoupled.
    """
    import httpx
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "http://localhost:8001/mcp",
            json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "ingest_document",
                    "arguments": {"document_id": req.document_id, "chunks": req.chunks},
                },
            },
        )
        resp.raise_for_status()
        data = resp.json()

    result = json.loads(data["result"]["content"][0]["text"])
    return result


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
async def health():
    redis_ok = await _redis.ping() if _redis else False
    return {"status": "ok", "redis": redis_ok}


# ---------------------------------------------------------------------------
# Run:  uvicorn main:app --reload --port 8000
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
