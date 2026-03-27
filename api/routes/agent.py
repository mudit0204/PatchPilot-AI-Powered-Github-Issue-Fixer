"""
AI Agent Route
Handles agent execution requests and streams progress
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import AsyncGenerator
import json

from models import AgentRunRequest, AgentStep

router = APIRouter()


@router.post("/run")
async def run_agent(request: AgentRunRequest):
    """
    Execute the PatchPilot agent on a GitHub issue.
    Returns a streaming response with real-time progress.
    
    Args:
        request: Agent run configuration
    """
    try:
        # Import here to avoid circular dependencies
        from agent.orchestrator import PatchPilotOrchestrator
        
        orchestrator = PatchPilotOrchestrator()
        
        async def event_generator() -> AsyncGenerator[str, None]:
            """Generate SSE events from agent steps"""
            try:
                async for step in orchestrator.run(request):
                    # Format as Server-Sent Events
                    event_data = step.model_dump_json()
                    yield f"data: {event_data}\n\n"
            except Exception as e:
                error_step = AgentStep(
                    step_type="error",
                    content=f"Agent execution failed: {str(e)}"
                )
                yield f"data: {error_step.model_dump_json()}\n\n"
        
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream"
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start agent: {str(e)}")


@router.post("/run-sync")
async def run_agent_sync(request: AgentRunRequest):
    """
    Execute the PatchPilot agent synchronously.
    Returns all results at once (not streaming).
    
    Args:
        request: Agent run configuration
    """
    try:
        from agent.orchestrator import PatchPilotOrchestrator
        
        orchestrator = PatchPilotOrchestrator()
        steps = []
        
        async for step in orchestrator.run(request):
            steps.append(step)
        
        return {
            "status": "success",
            "steps": [step.model_dump() for step in steps],
            "total_steps": len(steps)
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {str(e)}")


@router.get("/status/{run_id}")
async def get_agent_status(run_id: str):
    """
    Get the status of a running agent task.
    
    Args:
        run_id: Unique run identifier
    """
    # TODO: Implement status tracking
    return {
        "run_id": run_id,
        "status": "pending",
        "message": "Status tracking not yet implemented"
    }
