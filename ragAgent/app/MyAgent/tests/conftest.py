"""
Shared pytest fixtures for MyAgent tests.

Provides:
  - mock_run_pipeline_success: AsyncMock that returns a valid pipeline result
  - mock_run_pipeline_error:   AsyncMock that returns a pipeline-level error dict
  - mock_run_pipeline_raises:  AsyncMock that raises an exception (simulates crash)
"""

import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# Fixture: successful pipeline response
# ---------------------------------------------------------------------------

VALID_PIPELINE_RESULT = {
    "status": "passed",
    "answer": "AI refers to systems that perform tasks requiring human intelligence.",
    "confidence": 0.92,
    "cited_chunk_ids": ["chunk-001", "chunk-002"],
    "issues": [],
    "query": "What is AI?",
}


@pytest.fixture
def mock_run_pipeline_success():
    """Patches orchestrator.run_pipeline to return a valid result dict."""
    with patch(
        "orchestrator.run_pipeline",
        new_callable=AsyncMock,
        return_value=VALID_PIPELINE_RESULT,
    ) as mock:
        yield mock


# ---------------------------------------------------------------------------
# Fixture: pipeline returns an error status (no exception, but status=failed)
# ---------------------------------------------------------------------------

ERROR_PIPELINE_RESULT = {
    "status": "failed",
    "answer": None,
    "confidence": 0.0,
    "cited_chunk_ids": [],
    "issues": ["Retriever failed: connection timeout"],
    "error": "Retriever failed: connection timeout",
    "query": "What is AI?",
}


@pytest.fixture
def mock_run_pipeline_error():
    """Patches orchestrator.run_pipeline to return a failed-status result dict."""
    with patch(
        "orchestrator.run_pipeline",
        new_callable=AsyncMock,
        return_value=ERROR_PIPELINE_RESULT,
    ) as mock:
        yield mock


# ---------------------------------------------------------------------------
# Fixture: pipeline raises an unhandled exception
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_run_pipeline_raises():
    """Patches orchestrator.run_pipeline to raise a RuntimeError."""
    with patch(
        "orchestrator.run_pipeline",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Simulated pipeline crash"),
    ) as mock:
        yield mock
