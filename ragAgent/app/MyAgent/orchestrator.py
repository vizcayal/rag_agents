# graph/orchestrator.py
# LangGraph Orchestrator — wires Retriever → Reasoner → Validator
# Using a StateGraph to manage the agentic workflow.

from __future__ import annotations
import asyncio
import json
from typing import Any, AsyncIterator, TypedDict, Annotated, List
import operator


# ---------------------------------------------------------------------------
# State Definition
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    query: str
    top_k: int
    retrieved_chunks: Annotated[List[Any], operator.add]
    reasoner_output: Any
    validator_output: Any
    issues: Annotated[List[str], operator.add]
    status: str
    error: str


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def retrieve_node(state: AgentState):
    from retriever import RetrieverAgent
    print(f"[Graph] Node: Retrieve | Query: {state['query']}")
    
    try:
        agent = RetrieverAgent(top_k=state.get("top_k", 5))
        output = await agent.run(state["query"])
        
        if not output.chunks:
            return {"error": "No chunks retrieved", "status": "failed"}
            
        return {"retrieved_chunks": output.chunks}
    except Exception as e:
        return {"error": f"Retriever failed: {e}", "status": "failed"}


async def reason_node(state: AgentState):
    from reasoner import ReasonerAgent
    print(f"[Graph] Node: Reason | Chunks: {len(state['retrieved_chunks'])}")
    
    if state.get("error"):
        return {}

    try:
        agent = ReasonerAgent()
        output = await agent.run(
            query=state["query"],
            chunks=state["retrieved_chunks"]
        )
        return {"reasoner_output": output}
    except Exception as e:
        return {"error": f"Reasoner failed: {e}", "status": "failed"}


async def validate_node(state: AgentState):
    from validator import ValidatorAgent
    print(f"[Graph] Node: Validate")
    
    if state.get("error") or not state.get("reasoner_output"):
        return {}

    try:
        agent = ValidatorAgent()
        output = await agent.run(
            reasoner_output=state["reasoner_output"],
            retrieved_chunks=state["retrieved_chunks"]
        )
        return {
            "validator_output": output,
            "status": output.status.value,
            "issues": output.issues
        }
    except Exception as e:
        return {"error": f"Validator failed: {e}", "status": "failed"}


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------

def _build_graph():
    from langgraph.graph import StateGraph, END
    
    workflow = StateGraph(AgentState)
    
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("reason", reason_node)
    workflow.add_node("validate", validate_node)
    
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "reason")
    workflow.add_edge("reason", "validate")
    workflow.add_edge("validate", END)
    
    return workflow.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_pipeline(query: str, top_k: int = 5) -> dict:
    # Build graph inside the call to keep imports lazy
    app = _build_graph()
    
    initial_state = {
        "query": query,
        "top_k": top_k,
        "retrieved_chunks": [],
        "issues": [],
        "status": "started"
    }
    
    final_state = await app.ainvoke(initial_state)
    
    # Map graph state back to our standard response format
    if final_state.get("error"):
        return {
            "status": "failed",
            "error": final_state["error"],
            "query": query,
            "answer": None,
            "confidence": 0.0,
            "cited_chunk_ids": []
        }
        
    val = final_state.get("validator_output")
    return {
        "status": final_state.get("status", "failed"),
        "answer": val.answer if val else None,
        "confidence": round(val.confidence, 3) if val else 0.0,
        "cited_chunk_ids": val.cited_chunk_ids if val else [],
        "issues": final_state.get("issues", []),
        "query": query
    }


async def run_pipeline_stream(query: str, top_k: int = 5) -> AsyncIterator[str]:
    # Stream events from LangGraph
    app = _build_graph()
    
    initial_state = {
        "query": query,
        "top_k": top_k,
        "retrieved_chunks": [],
        "issues": [],
        "status": "started"
    }
    
    async for event in app.astream(initial_state):
        # LangGraph events look like: {'node_name': {'state_update': ...}}
        for node_name in event:
            yield json.dumps({"event": "stage", "stage": node_name}) + "\n"
    
    # Final result
    result = await run_pipeline(query, top_k)
    yield f'{{"event": "result", "data": {json.dumps(result)}}}\n'


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    # For local testing, ensure these are set
    # os.environ["KNOWLEDGE_BASE_ID"] = "HMMAFZE3WZ"
    # os.environ["AWS_REGION"] = "us-east-1"
    
    async def _test():
        query = "What documents are in my knowledge base?"
        print(f"--- Local Test: {query} ---")
        result = await run_pipeline(query, top_k=3)
        print(json.dumps(result, indent=2))

    asyncio.run(_test())
