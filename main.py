"""
PatchPilot Backend - Main FastAPI Application
AI-powered GitHub issue resolution system
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn

from api.routes import issues, agent, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    print("🚀 PatchPilot backend starting...")
    yield
    print("🛑 PatchPilot backend shutting down...")


app = FastAPI(
    title="PatchPilot API",
    description="AI-powered GitHub issue resolution using OpenHands + Gemini",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend (React) and middleware (Node.js) to communicate
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(issues.router, prefix="/api/issues", tags=["Issues"])
app.include_router(agent.router, prefix="/api/agent", tags=["Agent"])


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
