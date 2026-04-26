# mcp_servers/vectordb_server.py
# VectorDB MCP Server — exposes vector search as MCP tools.
# Backed by ChromaDB (swap for Qdrant by replacing the client section).

from __future__ import annotations
import json
import os
import ssl
import uuid

# ---------------------------------------------------------------------------
# Corporate proxy SSL workaround — must happen before any network-capable
# library (chromadb / huggingface_hub / httpx) is imported.
# ---------------------------------------------------------------------------
os.environ["HF_HUB_DISABLE_SSL_VERIFICATION"] = "1"
os.environ.setdefault("HUGGINGFACE_HUB_VERBOSITY", "warning")

# Patch httpx (used by huggingface_hub) to skip certificate verification.
import httpx
_original_init = httpx.Client.__init__

def _patched_init(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    _original_init(self, *args, **kwargs)

httpx.Client.__init__ = _patched_init  # type: ignore[method-assign]

# Also patch the async client used during downloads.
_original_async_init = httpx.AsyncClient.__init__

def _patched_async_init(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    _original_async_init(self, *args, **kwargs)

httpx.AsyncClient.__init__ = _patched_async_init  # type: ignore[method-assign]

ssl._create_default_https_context = ssl._create_unverified_context  # noqa: SLF001

import chromadb
from chromadb.utils import embedding_functions
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# ChromaDB setup
# ---------------------------------------------------------------------------

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_data")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "documents")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

_chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
_embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name=EMBEDDING_MODEL
)
_collection = _chroma_client.get_or_create_collection(
    name=COLLECTION_NAME,
    embedding_function=_embed_fn,
    metadata={"hnsw:space": "cosine"},
)

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def search_documents(query: str, top_k: int = 5) -> dict:
    """
    Embed the query and return the top-k most similar document chunks.
    Returns cosine similarity scores (higher = more similar).
    """
    results = _collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    ids       = results["ids"][0]
    docs      = results["documents"][0]
    metas     = results["metadatas"][0]
    distances = results["distances"][0]   # cosine distance: 0 = identical

    for cid, text, meta, dist in zip(ids, docs, metas, distances):
        chunks.append({
            "chunk_id":    cid,
            "document_id": meta.get("document_id", "unknown"),
            "text":        text,
            "score":       round(1 - dist, 4),   # convert distance → similarity
            "metadata":    meta,
        })

    return {"results": chunks}


def ingest_document(document_id: str, chunks: list[dict]) -> dict:
    """
    Ingest document chunks into the vector store.
    Each chunk: {"text": str, "metadata": dict (optional)}
    """
    ids      = [str(uuid.uuid4()) for _ in chunks]
    texts    = [c["text"] for c in chunks]
    metadatas = [
        {**c.get("metadata", {}), "document_id": document_id}
        for c in chunks
    ]

    _collection.add(ids=ids, documents=texts, metadatas=metadatas)
    return {"ingested": len(ids), "document_id": document_id, "chunk_ids": ids}


def delete_document(document_id: str) -> dict:
    """Remove all chunks belonging to a document."""
    _collection.delete(where={"document_id": document_id})
    return {"deleted": True, "document_id": document_id}


def list_documents() -> dict:
    """Return a summary of all documents in the collection."""
    all_metas = _collection.get(include=["metadatas"])["metadatas"]
    doc_ids = list({m.get("document_id", "unknown") for m in all_metas})
    return {"documents": doc_ids, "total_chunks": len(all_metas)}


# ---------------------------------------------------------------------------
# MCP JSON-RPC dispatcher
# ---------------------------------------------------------------------------

TOOLS = {
    "search_documents": {
        "fn": search_documents,
        "description": "Embed a query and retrieve the top-k similar document chunks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    "ingest_document": {
        "fn": ingest_document,
        "description": "Ingest document chunks into the vector store.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "chunks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "metadata": {"type": "object"},
                        },
                        "required": ["text"],
                    },
                },
            },
            "required": ["document_id", "chunks"],
        },
    },
    "delete_document": {
        "fn": delete_document,
        "description": "Delete all chunks for a given document_id.",
        "inputSchema": {
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
        },
    },
    "list_documents": {
        "fn": list_documents,
        "description": "List all ingested documents.",
        "inputSchema": {"type": "object", "properties": {}},
    },
}

# ---------------------------------------------------------------------------
# FastAPI app (MCP over HTTP)
# ---------------------------------------------------------------------------

app = FastAPI(title="VectorDB MCP Server")


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    body = await request.json()
    method  = body.get("method")
    req_id  = body.get("id", 1)
    params  = body.get("params", {})

    # --- tools/list ---
    if method == "tools/list":
        tools_list = [
            {
                "name": name,
                "description": meta["description"],
                "inputSchema": meta["inputSchema"],
            }
            for name, meta in TOOLS.items()
        ]
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools_list}})

    # --- tools/call ---
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
    return {"status": "ok", "collection": COLLECTION_NAME, "chunks": _collection.count()}


# ---------------------------------------------------------------------------
# Run:  uvicorn mcp_servers.vectordb_server:app --port 8001
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
