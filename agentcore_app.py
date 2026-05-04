"""
AgentCore Entrypoint — RAG Pipeline
Wraps the LangGraph orchestrator for deployment on Amazon Bedrock AgentCore Runtime.

The runtime calls `invoke(payload)` for each request.
Payload expected: {"prompt": "what is ai?"}
"""

import asyncio
import os
from bedrock_agentcore import BedrockAgentCoreApp

# Import your existing pipeline
from orchestrator import run_pipeline

# Configure the app
app = BedrockAgentCoreApp(
    name="rag-pipeline-agent",
    description="Multi-agent RAG pipeline powered by LangGraph",
)

@app.entrypoint
async def invoke(payload: dict) -> dict:
    """
    Main entrypoint called by AgentCore Runtime.
    """
    query = payload.get("prompt", "")
    top_k = payload.get("top_k", 5)
    
    if not query:
        return {"response": "Error: No prompt provided."}

    # AgentCore runs in AWS, so VECTORDB_URL must be a public/AWS-accessible URL
    # It cannot be localhost or host.docker.internal!
    print(f"Running pipeline for query: {query}")
    print(f"VectorDB URL configured as: {os.getenv('VECTORDB_URL')}")

    try:
        # Run the LangGraph pipeline
        result = await run_pipeline(query=query, top_k=top_k)
        
        # Format the response for AgentCore
        return {
            "response": result.get("answer", result.get("error", "No answer generated")),
            "confidence": result.get("confidence", 0.0),
            "cited_chunks": result.get("cited_chunk_ids", []),
            "status": result.get("status", "unknown")
        }
    except Exception as e:
        return {"response": f"Pipeline failed: {str(e)}"}
