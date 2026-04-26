# mcp_servers/claude_server.py
# Claude MCP Server — wraps the Anthropic API as an MCP tool endpoint.
# Exposes: complete (standard), stream_complete (SSE), and embed.

from __future__ import annotations
import json
import os
from typing import AsyncIterator
from json import JSONDecodeError

# ---------------------------------------------------------------------------
# Corporate proxy SSL workaround — must happen before Anthropic/httpx import.
# ---------------------------------------------------------------------------
os.environ.setdefault("HUGGINGFACE_HUB_VERBOSITY", "warning")
os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFICATION", "1")

import ssl
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

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "Missing ANTHROPIC_API_KEY. In PowerShell run: "
        "$env:ANTHROPIC_API_KEY='your_key_here'"
    )
DEFAULT_MODEL     = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
MAX_TOKENS        = int(os.getenv("MAX_TOKENS", "4096"))

_anthropic_http_client = anthropic.DefaultAsyncHttpxClient(
    verify=False,
    trust_env=True,
)
_client = anthropic.AsyncAnthropic(
    api_key=ANTHROPIC_API_KEY,
    http_client=_anthropic_http_client,
)


def _format_exception(exc: Exception) -> str:
    """Include exception type and root cause for easier MCP debugging."""
    parts = [f"{type(exc).__name__}: {exc}"]
    cause = exc.__cause__
    if cause:
        parts.append(f"caused by {type(cause).__name__}: {cause}")
    context = exc.__context__
    if context and context is not cause:
        parts.append(f"context {type(context).__name__}: {context}")
    return " | ".join(parts)

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def complete(
    prompt: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = MAX_TOKENS,
    temperature: float = 0.3,
    system: str | None = None,
) -> dict:
    """
    Standard (non-streaming) completion.
    Returns the full text response plus token usage.
    """
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    response = await _client.messages.create(**kwargs)

    text = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )
    return {
        "text": text,
        "model": response.model,
        "usage": {
            "input_tokens":  response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        "stop_reason": response.stop_reason,
    }


async def stream_complete(
    prompt: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = MAX_TOKENS,
    temperature: float = 0.3,
    system: str | None = None,
) -> AsyncIterator[str]:
    """
    Streaming completion — yields text delta chunks.
    Used internally by the streaming FastAPI endpoint.
    """
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system

    async with _client.messages.stream(**kwargs) as stream:
        async for text in stream.text_stream:
            yield text


async def count_tokens(prompt: str, model: str = DEFAULT_MODEL) -> dict:
    """Estimate token count for a prompt without running a completion."""
    response = await _client.messages.count_tokens(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return {"input_tokens": response.input_tokens}


# ---------------------------------------------------------------------------
# MCP JSON-RPC dispatcher
# ---------------------------------------------------------------------------

TOOLS = {
    "complete": {
        "description": "Run a non-streaming Claude completion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt":      {"type": "string"},
                "model":       {"type": "string"},
                "max_tokens":  {"type": "integer"},
                "temperature": {"type": "number"},
                "system":      {"type": "string"},
            },
            "required": ["prompt"],
        },
    },
    "count_tokens": {
        "description": "Count tokens for a prompt without completing it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "model":  {"type": "string"},
            },
            "required": ["prompt"],
        },
    },
}

app = FastAPI(title="Claude MCP Server")


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except JSONDecodeError:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error: request body must be valid JSON.",
                },
            },
            status_code=400,
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": "Invalid Request: root JSON value must be an object.",
                },
            },
            status_code=400,
        )

    method  = body.get("method")
    req_id  = body.get("id", 1)
    params  = body.get("params", {})

    # --- tools/list ---
    if method == "tools/list":
        tools_list = [
            {"name": name, "description": meta["description"], "inputSchema": meta["inputSchema"]}
            for name, meta in TOOLS.items()
        ]
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools_list}})

    # --- tools/call ---
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "complete":
            try:
                result = await complete(**arguments)
                return JSONResponse({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
                })
            except Exception as exc:
                return JSONResponse({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32000, "message": _format_exception(exc)},
                })

        if tool_name == "count_tokens":
            try:
                result = await count_tokens(**arguments)
                return JSONResponse({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
                })
            except Exception as exc:
                return JSONResponse({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32000, "message": _format_exception(exc)},
                })

        return JSONResponse({
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
        })

    return JSONResponse({
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    })


# ---------------------------------------------------------------------------
# Dedicated streaming endpoint (bypasses JSON-RPC for efficiency)
# ---------------------------------------------------------------------------

@app.post("/stream")
async def stream_endpoint(request: Request):
    """
    SSE streaming endpoint.
    Body: {"prompt": str, "model": str, "max_tokens": int, "temperature": float}
    """
    try:
        body = await request.json()
    except JSONDecodeError:
        return JSONResponse(
            {"error": "Parse error: request body must be valid JSON."},
            status_code=400,
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {"error": "Invalid request: root JSON value must be an object."},
            status_code=400,
        )

    async def event_generator():
        try:
            async for chunk in stream_complete(**body):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/health")
def health():
    return {"status": "ok", "default_model": DEFAULT_MODEL}


# ---------------------------------------------------------------------------
# Run:  uvicorn mcp_servers.claude_server:app --port 8002
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
