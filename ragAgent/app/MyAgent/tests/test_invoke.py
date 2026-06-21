"""
Unit tests for the invoke() entrypoint in main.py.

Test cases:
  1. Happy path — valid prompt returns structured response
  2. Missing top_k — defaults to 5
  3. Empty prompt — returns error without calling the pipeline
  4. Pipeline returns error status — response field is populated from error
  5. Pipeline raises exception — fallback response is a non-empty string
"""

import sys
import os
import pytest

# Add the app directory to sys.path so `import main` resolves correctly
# (main.py lives alongside orchestrator.py, retriever.py, etc.)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# We need to stub out BedrockAgentCoreApp at import time because it tries
# to connect to the AgentCore service when instantiated.
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch, AsyncMock

# Stub the bedrock_agentcore module before importing main
bedrock_stub = MagicMock()

# BedrockAgentCoreApp() must return an object whose .entrypoint() decorator
# is a no-op pass-through, and .run() does nothing.
app_instance = MagicMock()
app_instance.entrypoint = lambda fn: fn   # decorator just returns the function
app_instance.run = MagicMock()
bedrock_stub.BedrockAgentCoreApp.return_value = app_instance

sys.modules["bedrock_agentcore"] = bedrock_stub

# Now safe to import invoke from main
from main import invoke  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _invoke(payload: dict) -> dict:
    """Thin wrapper so tests read naturally."""
    return await invoke(payload)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_returns_all_fields(mock_run_pipeline_success):
    """Valid prompt → all expected response fields are present and correct."""
    result = await _invoke({"prompt": "What is AI?", "top_k": 3})

    assert isinstance(result, dict), "invoke() must return a dict"
    assert "response" in result,       "response field must be present"
    assert "confidence" in result,     "confidence field must be present"
    assert "cited_chunks" in result,   "cited_chunks field must be present"
    assert "status" in result,         "status field must be present"

    assert isinstance(result["response"], str),  "response must be a string"
    assert len(result["response"]) > 0,          "response must be non-empty"
    assert result["confidence"] == 0.92
    assert result["cited_chunks"] == ["chunk-001", "chunk-002"]
    assert result["status"] == "passed"

    # Verify top_k was forwarded to the pipeline
    mock_run_pipeline_success.assert_awaited_once_with(query="What is AI?", top_k=3)


@pytest.mark.asyncio
async def test_missing_top_k_defaults_to_5(mock_run_pipeline_success):
    """When top_k is not in payload, pipeline is called with top_k=5."""
    await _invoke({"prompt": "What is AI?"})

    mock_run_pipeline_success.assert_awaited_once_with(query="What is AI?", top_k=5)


@pytest.mark.asyncio
async def test_empty_prompt_returns_error_without_calling_pipeline(mock_run_pipeline_success):
    """Empty prompt short-circuits before the pipeline is called."""
    result = await _invoke({"prompt": ""})

    assert "response" in result
    assert "Error" in result["response"] or "error" in result["response"].lower()
    mock_run_pipeline_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_error_status_populates_response(mock_run_pipeline_error):
    """When pipeline returns status=failed, invoke() still returns a non-empty response."""
    result = await _invoke({"prompt": "What is AI?"})

    assert "response" in result
    assert isinstance(result["response"], str)
    assert len(result["response"]) > 0, "response must be non-empty even on pipeline error"


@pytest.mark.asyncio
async def test_pipeline_exception_returns_fallback_response(mock_run_pipeline_raises):
    """When pipeline raises, invoke() catches it and returns a non-empty response string."""
    result = await _invoke({"prompt": "What is AI?"})

    assert "response" in result
    assert isinstance(result["response"], str)
    assert len(result["response"]) > 0, "fallback response must be non-empty"
    # The fallback message from main.py starts with "Pipeline failed:"
    assert "Pipeline failed" in result["response"] or "failed" in result["response"].lower()
