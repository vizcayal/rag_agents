# mcp_servers/schema_server.py
# Schema MCP Server — Pydantic schema validation + lightweight grounding check.
# The grounding check uses sentence-transformers cosine similarity to score
# how well each cited chunk supports the answer (no LLM call needed).

from __future__ import annotations
import json
import os
import ssl
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Corporate proxy SSL workaround — must happen before sentence-transformers
# (and huggingface_hub/httpx) are imported.
# ---------------------------------------------------------------------------
os.environ["HF_HUB_DISABLE_SSL_VERIFICATION"] = "1"
os.environ.setdefault("HUGGINGFACE_HUB_VERBOSITY", "warning")

import httpx
_original_httpx_client_init = httpx.Client.__init__


def _patched_httpx_client_init(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    _original_httpx_client_init(self, *args, **kwargs)


httpx.Client.__init__ = _patched_httpx_client_init  # type: ignore[method-assign]

_original_httpx_async_client_init = httpx.AsyncClient.__init__


def _patched_httpx_async_client_init(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    _original_httpx_async_client_init(self, *args, **kwargs)


httpx.AsyncClient.__init__ = _patched_httpx_async_client_init  # type: ignore[method-assign]
ssl._create_default_https_context = ssl._create_unverified_context  # noqa: SLF001

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Embedding model for grounding checks
# ---------------------------------------------------------------------------

EMBED_MODEL = os.getenv("GROUNDING_MODEL", "all-MiniLM-L6-v2")
_embedder = SentenceTransformer(EMBED_MODEL)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


# ---------------------------------------------------------------------------
# Registered Pydantic schemas
# ---------------------------------------------------------------------------

class QueryResponse(BaseModel):
    answer: str
    cited_chunk_ids: list[str]
    query: str


class IngestRequest(BaseModel):
    document_id: str
    chunks: list[dict]


SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "QueryResponse":  QueryResponse,
    "IngestRequest":  IngestRequest,
}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def validate_schema(data: dict, schema_name: str) -> dict:
    """
    Validate a data dict against a registered Pydantic schema.
    Returns {valid: bool, errors: list[str]}.
    """
    if schema_name not in SCHEMA_REGISTRY:
        return {
            "valid": False,
            "errors": [f"Schema '{schema_name}' not found. "
                       f"Available: {list(SCHEMA_REGISTRY.keys())}"],
        }

    schema_cls = SCHEMA_REGISTRY[schema_name]
    try:
        schema_cls(**data)
        return {"valid": True, "errors": []}
    except ValidationError as exc:
        errors = [f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return {"valid": False, "errors": errors}


def check_grounding(answer: str, chunks: list[dict]) -> dict:
    """
    Score how well each chunk supports the answer using cosine similarity
    between their sentence embeddings.

    chunks: [{"chunk_id": str, "text": str}, ...]
    Returns: {"scores": [{"chunk_id": str, "score": float}, ...]}
    """
    if not chunks:
        return {"scores": []}

    texts = [answer] + [c["text"] for c in chunks]
    embeddings = _embedder.encode(texts, normalize_embeddings=True)

    answer_emb = embeddings[0]
    scores = []
    for i, chunk in enumerate(chunks):
        chunk_emb = embeddings[i + 1]
        score = _cosine(answer_emb, chunk_emb)
        scores.append({"chunk_id": chunk["chunk_id"], "score": round(score, 4)})

    return {"scores": scores}


def list_schemas() -> dict:
    """List all registered schema names."""
    return {"schemas": list(SCHEMA_REGISTRY.keys())}


def get_schema(schema_name: str) -> dict:
    """Return the JSON Schema for a registered Pydantic model."""
    if schema_name not in SCHEMA_REGISTRY:
        return {"error": f"Schema '{schema_name}' not found."}
    return {"schema": SCHEMA_REGISTRY[schema_name].model_json_schema()}


# ---------------------------------------------------------------------------
# MCP JSON-RPC dispatcher
# ---------------------------------------------------------------------------

TOOLS = {
    "validate_schema": {
        "fn": validate_schema,
        "description": "Validate a data dict against a registered Pydantic schema.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data":        {"type": "object"},
                "schema_name": {"type": "string"},
            },
            "required": ["data", "schema_name"],
        },
    },
    "check_grounding": {
        "fn": check_grounding,
        "description": "Score how well each chunk supports an answer (cosine similarity).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "chunks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "chunk_id": {"type": "string"},
                            "text":     {"type": "string"},
                        },
                        "required": ["chunk_id", "text"],
                    },
                },
            },
            "required": ["answer", "chunks"],
        },
    },
    "list_schemas": {
        "fn": list_schemas,
        "description": "List all registered schema names.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "get_schema": {
        "fn": get_schema,
        "description": "Return the JSON Schema for a registered Pydantic model.",
        "inputSchema": {
            "type": "object",
            "properties": {"schema_name": {"type": "string"}},
            "required": ["schema_name"],
        },
    },
}

app = FastAPI(title="Schema MCP Server")


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    body   = await request.json()
    method = body.get("method")
    req_id = body.get("id", 1)
    params = body.get("params", {})

    if method == "tools/list":
        tools_list = [
            {"name": name, "description": meta["description"], "inputSchema": meta["inputSchema"]}
            for name, meta in TOOLS.items()
        ]
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools_list}})

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in TOOLS:
            return JSONResponse({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
            })

        try:
            result = TOOLS[tool_name]["fn"](**arguments)
            return JSONResponse({
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
            })
        except Exception as exc:
            return JSONResponse({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32000, "message": str(exc)},
            })

    return JSONResponse({
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    })


@app.get("/health")
def health():
    return {"status": "ok", "schemas": list(SCHEMA_REGISTRY.keys())}


# ---------------------------------------------------------------------------
# Run:  uvicorn mcp_servers.schema_server:app --port 8003
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
