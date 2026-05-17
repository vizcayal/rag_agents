"""
AgentCore Entrypoint — RAG Pipeline
Wraps the orchestrator for deployment on Amazon Bedrock AgentCore Runtime.

The runtime calls `invoke(payload)` for each request.
Payload expected: {"prompt": "what is ai?"}
"""

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from bedrock_agentcore import BedrockAgentCoreApp

# Configure the app
app = BedrockAgentCoreApp()

@app.entrypoint
async def invoke(payload: dict) -> dict:
    """
    Main entrypoint called by AgentCore Runtime.
    """
    # Lazy import — keeps initialization under 30s
    from orchestrator import run_pipeline

    query = payload.get("prompt", "")
    top_k = payload.get("top_k", 5)
    
    if not query:
        return {"response": "Error: No prompt provided."}

    logger.info(f"Running pipeline for query: {query}")

    try:
        # Run the pipeline
        result = await run_pipeline(query=query, top_k=top_k)
        
        logger.info(f"Pipeline result status: {result.get('status')}")
        if result.get("error"):
            logger.error(f"Pipeline error: {result.get('error')}")
        
        # Format the response for AgentCore
        return {
            "response": result.get("answer", result.get("error", "No answer generated")),
            "confidence": result.get("confidence", 0.0),
            "cited_chunks": result.get("cited_chunk_ids", []),
            "status": result.get("status", "unknown"),
            "error": result.get("error")
        }
    except Exception as e:
        logger.exception(f"Pipeline exception: {e}")
        return {"response": f"Pipeline failed: {str(e)}"}


# In Container mode, we must explicitly start the server
if __name__ == "__main__":
    app.run()

# Force redeploy
