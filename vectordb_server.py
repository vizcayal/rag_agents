# vectordb_server.py
# Minimal VectorDB server — exposes ChromaDB via two REST endpoints:
#   POST /search   → find similar chunks
#   POST /ingest   → add document chunks
# Run: uv run uvicorn vectordb_server:app --port 8001

from __future__ import annotations
import os
import ssl
import uuid

# ---------------------------------------------------------------------------
# Corporate proxy SSL workaround — must run before chromadb / huggingface
# ---------------------------------------------------------------------------
os.environ["HF_HUB_DISABLE_SSL_VERIFICATION"] = "1"

import httpx

_orig = httpx.Client.__init__
def _no_verify(self, *a, **kw):
    kw.setdefault("verify", False)
    _orig(self, *a, **kw)
httpx.Client.__init__ = _no_verify  # type: ignore[method-assign]

_orig_async = httpx.AsyncClient.__init__
def _no_verify_async(self, *a, **kw):
    kw.setdefault("verify", False)
    _orig_async(self, *a, **kw)
httpx.AsyncClient.__init__ = _no_verify_async  # type: ignore[method-assign]

ssl._create_default_https_context = ssl._create_unverified_context  # noqa: SLF001

# ---------------------------------------------------------------------------
# ChromaDB setup
# ---------------------------------------------------------------------------
import chromadb
from chromadb.utils import embedding_functions
from fastapi import FastAPI
from pydantic import BaseModel, Field

CHROMA_PATH      = os.getenv("CHROMA_PATH", "./chroma_data")
COLLECTION_NAME  = os.getenv("CHROMA_COLLECTION", "documents")
EMBEDDING_MODEL  = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

_client     = chromadb.PersistentClient(path=CHROMA_PATH)
_embed_fn   = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
_collection = _client.get_or_create_collection(
    name=COLLECTION_NAME,
    embedding_function=_embed_fn,
    metadata={"hnsw:space": "cosine"},
)

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(5, ge=1, le=20)

class IngestRequest(BaseModel):
    document_id: str
    chunks: list[dict] = Field(..., description='[{"text": str, "metadata": dict}]')

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="VectorDB Server", version="1.0.0")


@app.post("/search")
def search(req: SearchRequest) -> dict:
    """Return the top-k chunks most similar to the query."""
    results = _collection.query(
        query_texts=[req.query],
        n_results=req.top_k,
        include=["documents", "metadatas", "distances"],
    )
    chunks = []
    for cid, text, meta, dist in zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append({
            "chunk_id":    cid,
            "document_id": meta.get("document_id", "unknown"),
            "text":        text,
            "score":       round(1 - dist, 4),  # cosine distance → similarity
            "metadata":    meta,
        })
    return {"results": chunks}


@app.post("/ingest", status_code=201)
def ingest(req: IngestRequest) -> dict:
    """Add document chunks to the vector store."""
    ids       = [str(uuid.uuid4()) for _ in req.chunks]
    texts     = [c["text"] for c in req.chunks]
    metadatas = [{**c.get("metadata", {}), "document_id": req.document_id} for c in req.chunks]
    _collection.add(ids=ids, documents=texts, metadatas=metadatas)
    return {"ingested": len(ids), "document_id": req.document_id, "chunk_ids": ids}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "collection": COLLECTION_NAME, "chunks": _collection.count()}
