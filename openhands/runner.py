"""
PatchPilot OpenHands Runner
Manages OpenHands agent execution inside a Docker container.
OpenHands (formerly OpenDevin) handles the agentic loop: browsing code,
running commands, and applying fixes autonomously.
"""

import asyncio
import json
import uuid
import os
from typing import AsyncGenerator, Optional
import docker
import httpx

from config import get_settings
from models import AgentStep, StepType

settings = get_settings()

OPENHANDS_PORT = 3000   # Internal port OpenHands listens on


class OpenHandsRunner:
    """
    Spins up an OpenHands Docker container, sends a task,
    and streams back the agent's reasoning steps via SSE.
    """

    def __init__(self):
        self.docker_client = docker.from_env()
        self._container = None
        self._host_port: Optional[int] = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start_container(self, workspace_path: str) -> str:
        """
        Start the OpenHands Docker container.
        Returns the container ID.
        """
        os.makedirs(workspace_path, exist_ok=True)

        # Check if already running
        if self._container:
            return self._container.id

        self._host_port = self._find_free_port()

        print(f"🐳 Starting OpenHands container on port {self._host_port}...")

        self._container = self.docker_client.containers.run(
            image=settings.OPENHANDS_IMAGE,
            name=f"patchpilot-openhands-{uuid.uuid4().hex[:6]}",
            detach=True,
            remove=True,   # Auto-remove when stopped
            ports={f"{OPENHANDS_PORT}/tcp": self._host_port},
            volumes={
                workspace_path: {"bind": settings.OPENHANDS_WORKSPACE, "mode": "rw"},
                # Mount Docker socket so OpenHands can spin up its own sandbox
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
            },
            environment={
                "LLM_MODEL": f"google/{settings.GEMINI_MODEL}",
                "LLM_API_KEY": settings.GEMINI_API_KEY,
                "SANDBOX_RUNTIME_CONTAINER_IMAGE": "ghcr.io/all-hands-ai/runtime:main",
                "LOG_ALL_EVENTS": "true",
            },
        )

        print(f"✅ OpenHands container started: {self._container.short_id}")
        return self._container.id

    def stop_container(self):
        """Stop and remove the OpenHands container."""
        if self._container:
            print(f"🛑 Stopping OpenHands container {self._container.short_id}...")
            try:
                self._container.stop(timeout=10)
            except Exception as e:
                print(f"Warning: container stop error: {e}")
            self._container = None

    # ── Task Execution ────────────────────────────────────────────────────

    async def run_task(
        self,
        task_prompt: str,
        workspace_path: str,
    ) -> AsyncGenerator[AgentStep, None]:
        """
        Send a task to the running OpenHands agent and stream back steps.

        OpenHands exposes a REST API at /api/conversations and an SSE stream
        at /api/conversations/{id}/events.
        """
        base_url = f"http://localhost:{self._host_port}"

        # Wait for container to be ready
        await self._wait_for_ready(base_url)

        # Create a new conversation/session
        conversation_id = await self._create_conversation(base_url, task_prompt)
        print(f"📝 OpenHands conversation started: {conversation_id}")

        # Stream events from OpenHands SSE endpoint
        async for step in self._stream_events(base_url, conversation_id):
            yield step

    async def _wait_for_ready(self, base_url: str, timeout: int = 120):
        """Poll until OpenHands server is up and accepting requests."""
        print("⏳ Waiting for OpenHands to be ready...")
        deadline = asyncio.get_event_loop().time() + timeout

        async with httpx.AsyncClient() as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    resp = await client.get(f"{base_url}/api/options/models", timeout=5)
                    if resp.status_code == 200:
                        print("✅ OpenHands is ready!")
                        return
                except Exception:
                    pass
                await asyncio.sleep(3)

        raise TimeoutError("OpenHands container did not become ready in time.")

    async def _create_conversation(self, base_url: str, task: str) -> str:
        """Create a new conversation in OpenHands and return the conversation ID."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/api/conversations",
                json={
                    "initial_user_message": task,
                    "runtime_type": "docker",
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["conversation_id"]

    async def _stream_events(
        self, base_url: str, conversation_id: str
    ) -> AsyncGenerator[AgentStep, None]:
        """
        Stream Server-Sent Events from OpenHands and translate them to AgentSteps.
        OpenHands event types: AgentThinkObservation, CmdRunAction, IPythonRunCellAction, etc.
        """
        url = f"{base_url}/api/conversations/{conversation_id}/events"

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue

                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        break

                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    step = self._parse_event(event)
                    if step:
                        yield step

                        # Stop streaming once the agent finishes
                        if event.get("type") == "AgentFinishAction":
                            break

    def _parse_event(self, event: dict) -> Optional[AgentStep]:
        """Convert an OpenHands event dict into an AgentStep."""
        event_type = event.get("type", "")
        content = event.get("message") or event.get("content") or event.get("command") or ""

        type_map = {
            "AgentThinkAction":          StepType.THOUGHT,
            "AgentFinishAction":         StepType.RESULT,
            "CmdRunAction":              StepType.ACTION,
            "IPythonRunCellAction":      StepType.ACTION,
            "FileEditAction":            StepType.ACTION,
            "FileReadAction":            StepType.ACTION,
            "CmdOutputObservation":      StepType.RESULT,
            "IPythonRunCellObservation": StepType.RESULT,
            "FileEditObservation":       StepType.PATCH,
            "ErrorObservation":          StepType.ERROR,
        }

        step_type = type_map.get(event_type)
        if not step_type or not content:
            return None

        return AgentStep(
            step_type=step_type,
            content=str(content),
            metadata={"event_type": event_type},
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _find_free_port() -> int:
        """Find an available local port."""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]
