# graph/orchestrator.py
# LangGraph Orchestrator — defines the StateGraph that wires
# Retriever → Reasoner → Validator and manages shared pipeline state.

from __future__ import annotations
import asyncio
from typing import Annotated, Any, AsyncIterator, TypedDict

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

from retriever import RetrieverAgent, RetrieverOutput
from reasoner import ReasonerAgent, ReasonerOutput
from validator import ValidatorAgent, ValidatorOutput, ValidationStatus


# ---------------------------------------------------------------------------
# Shared pipeline state
# Passed between nodes; each node reads what it needs and writes its output.
# ---------------------------------------------------------------------------

class PipelineState(TypedDict):
    # Input
    query:   str
    top_k:   int

    # Intermediate outputs (populated as pipeline progresses)
    retriever_output:  RetrieverOutput  | None
    reasoner_output:   ReasonerOutput   | None
    validator_output:  ValidatorOutput  | None

    # Final response (set after validation)
    final_answer:      str | None
    confidence:        float
    status:            str   # "pending" | "passed" | "warning" | "failed"
    error:             str | None


# ---------------------------------------------------------------------------
# Node functions
# Each node receives the full state, does its work, and returns a dict
# with the keys it wants to update.
# ---------------------------------------------------------------------------

async def retrieve_node(state: PipelineState) -> dict:
    """Node 1 — Retriever Agent."""
    try:
        agent = RetrieverAgent(top_k=state.get("top_k", 5))
        output = await agent.run(state["query"])
        return {"retriever_output": output, "error": None}
    except Exception as exc:
        return {"error": f"Retriever failed: {exc}", "status": "failed"}


async def reason_node(state: PipelineState) -> dict:
    """Node 2 — Reasoner Agent. Skipped if retrieval failed."""
    if state.get("error"):
        return {}   # propagate the error, skip this node

    retriever_output = state["retriever_output"]
    if not retriever_output or not retriever_output.chunks:
        return {
            "error": "No chunks retrieved — cannot reason.",
            "status": "failed",
        }

    try:
        agent = ReasonerAgent()
        output = await agent.run(
            query=state["query"],
            chunks=retriever_output.chunks,
        )
        return {"reasoner_output": output}
    except Exception as exc:
        return {"error": f"Reasoner failed: {exc}", "status": "failed"}


async def validate_node(state: PipelineState) -> dict:
    """Node 3 — Validator Agent. Skipped if any prior step failed."""
    if state.get("error"):
        return {}

    reasoner_output  = state["reasoner_output"]
    retriever_output = state["retriever_output"]

    try:
        agent = ValidatorAgent()
        output = await agent.run(
            reasoner_output=reasoner_output,
            retrieved_chunks=retriever_output.chunks,
        )
        return {
            "validator_output": output,
            "final_answer":     output.answer,
            "confidence":       output.confidence,
            "status":           output.status.value,
        }
    except Exception as exc:
        return {"error": f"Validator failed: {exc}", "status": "failed"}


# ---------------------------------------------------------------------------
# Conditional routing
# After validation, decide whether to END or retry (future extension).
# ---------------------------------------------------------------------------

def route_after_validation(state: PipelineState) -> str:
    # Always end — retry logic can be added here once 'retries' is added to PipelineState
    return END


# ---------------------------------------------------------------------------
# Build the StateGraph
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    graph = StateGraph(PipelineState)

    # Register nodes
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("reason",   reason_node)
    graph.add_node("validate", validate_node)

    # Linear edges
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "reason")
    graph.add_edge("reason",   "validate")

    # Conditional exit after validation
    graph.add_conditional_edges(
        "validate",
        route_after_validation,
        {END: END},
    )

    return graph.compile()


# Compiled graph — import and call from main.py
_compiled_graph = build_graph()


# ---------------------------------------------------------------------------
# Public run_pipeline interface
# ---------------------------------------------------------------------------

async def run_pipeline(query: str, top_k: int = 5) -> dict:
    """
    Run the full Retriever → Reasoner → Validator pipeline.
    Returns a dict ready to be serialized as the API response.
    """
    initial_state: PipelineState = {
        "query":             query,
        "top_k":             top_k,
        "retriever_output":  None,
        "reasoner_output":   None,
        "validator_output":  None,
        "final_answer":      None,
        "confidence":        0.0,
        "status":            "pending",
        "error":             None,
    }

    final_state = await _compiled_graph.ainvoke(initial_state)

    if final_state.get("error"):
        return {
            "status":     "failed",
            "error":      final_state["error"],
            "answer":     None,
            "confidence": 0.0,
            "cited_chunk_ids": [],
        }

    validator_out = final_state["validator_output"]
    return {
        "status":     final_state["status"],
        "answer":     final_state["final_answer"],
        "confidence": round(final_state["confidence"], 3),
        "cited_chunk_ids":   validator_out.cited_chunk_ids if validator_out else [],
        "issues":            validator_out.issues if validator_out else [],
        "query":             query,
    }


async def run_pipeline_stream(query: str, top_k: int = 5) -> AsyncIterator[str]:
    """
    Stream the pipeline — yields status updates as JSON strings,
    then the final answer token-by-token (if Reasoner supports streaming).

    For now yields stage announcements + the final answer.
    Extend by wiring the Reasoner's stream_complete path here.
    """
    yield '{"event": "stage", "stage": "retrieve"}\n'
    initial_state: PipelineState = {
        "query": query, "top_k": top_k,
        "retriever_output": None, "reasoner_output": None,
        "validator_output": None, "final_answer": None,
        "confidence": 0.0, "status": "pending", "error": None,
    }

    # Stream LangGraph events
    async for event in _compiled_graph.astream(initial_state):
        node_name = list(event.keys())[0]
        yield f'{{"event": "stage", "stage": "{node_name}"}}\n'

    # After streaming, get the final result
    result = await run_pipeline(query, top_k)
    import json
    yield f'{{"event": "result", "data": {json.dumps(result)}}}\n'


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    async def _test():
        result = await run_pipeline(
            query="What are the penalty clauses?",
            top_k=3,
        )
        import json
        print(json.dumps(result, indent=2))

    asyncio.run(_test())
