"""
Health Check Route
"""

from fastapi import APIRouter
from datetime import datetime

router = APIRouter()


@router.get("/")
async def health_check():
    """
    Basic health check endpoint.
    Returns system status and timestamp.
    """
    return {
        "status": "healthy",
        "service": "PatchPilot Backend",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0"
    }


@router.get("/ready")
async def readiness_check():
    """
    Readiness probe for container orchestration.
    Can be extended to check dependencies (DB, APIs, etc.)
    """
    return {
        "ready": True,
        "timestamp": datetime.utcnow().isoformat()
    }
