"""
PatchPilot OpenHands Runner
Manages OpenHands agent execution inside a Docker container.
OpenHands (formerly OpenDevin) handles the agentic loop: browsing code,
running commands, and applying fixes autonomously.
"""

import asyncio
import json
import sys
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
        self._container = None
        self._host_port: Optional[int] = None
        self._docker_client = None

    # ── Docker client (lazy init) ────────────────────────────────────────

    def _get_docker_client(self):
        """Get or create Docker client, handling Windows/Linux differences."""
        if self._docker_client is not None:
            return self._docker_client

        try:
            self._docker_client = docker.from_env()
            # Quick sanity check
            self._docker_client.ping()
            return self._docker_client
        except Exception as e:
            raise RuntimeError(
                f"Cannot connect to Docker daemon: {e}\n"
                f"Please ensure Docker Desktop is running."
            ) from e

    @staticmethod
    def _normalize_provider() -> str:
        return (settings.LLM_PROVIDER or "ollama").strip().lower()

    def _openhands_model_name(self) -> str:
        provider = self._normalize_provider()
        if provider == "ollama":
            return f"ollama/{settings.OLLAMA_MODEL}"
        return f"google/{settings.GEMINI_MODEL}"

    def _container_environment(self) -> dict:
        provider = self._normalize_provider()
        env = {
            "LLM_MODEL": self._openhands_model_name(),
            "SANDBOX_RUNTIME_CONTAINER_IMAGE": "ghcr.io/all-hands-ai/runtime:main",
            "LOG_ALL_EVENTS": "true",
        }

        if provider == "ollama":
            env["LLM_BASE_URL"] = settings.OLLAMA_BASE_URL_DOCKER
            env["LLM_API_KEY"] = "ollama"
        else:
            env["LLM_API_KEY"] = settings.GEMINI_API_KEY

        # Pass GitHub token to OpenHands so it can interact with PRs/Issues if needed
        if settings.GITHUB_TOKEN:
            env["GITHUB_TOKEN"] = settings.GITHUB_TOKEN

        return env

    def _get_volumes(self, workspace_path: str) -> dict:
        """Build volume mounts, handling Windows vs Linux Docker socket."""
        volumes = {
            workspace_path: {"bind": settings.OPENHANDS_WORKSPACE, "mode": "rw"},
        }

        if sys.platform == "win32":
            # Windows Docker Desktop — mount the named pipe so OpenHands
            # can spawn its own sandbox containers.
            pipe = "//./pipe/docker_engine"
            volumes[pipe] = {"bind": "/var/run/docker.sock", "mode": "rw"}
        else:
            # Linux / macOS
            volumes["/var/run/docker.sock"] = {
                "bind": "/var/run/docker.sock",
                "mode": "rw",
            }

        return volumes

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start_container(self, workspace_path: str) -> str:
        """
        Start the OpenHands Docker container.
        Returns the container ID.
        Raises RuntimeError with a human-readable message on failure.
        """
        os.makedirs(workspace_path, exist_ok=True)

        # Check if already running
        if self._container:
            return self._container.id

        client = self._get_docker_client()
        self._host_port = self._find_free_port()

        container_name = f"patchpilot-openhands-{uuid.uuid4().hex[:6]}"
        print(f"🐳 Starting OpenHands container '{container_name}' on port {self._host_port}...")

        try:
            # Ensure image is available
            try:
                client.images.get(settings.OPENHANDS_IMAGE)
            except docker.errors.ImageNotFound:
                print(f"📥 Pulling OpenHands image {settings.OPENHANDS_IMAGE} (this may take a while)...")
                client.images.pull(settings.OPENHANDS_IMAGE)

            self._container = client.containers.run(
                image=settings.OPENHANDS_IMAGE,
                name=container_name,
                detach=True,
                remove=True,   # Auto-remove when stopped
                ports={f"{OPENHANDS_PORT}/tcp": self._host_port},
                volumes=self._get_volumes(workspace_path),
                environment=self._container_environment(),
                extra_hosts={"host.docker.internal": "host-gateway"},
            )

            print(f"✅ OpenHands container started: {self._container.short_id}")
            return self._container.id

        except docker.errors.APIError as e:
            raise RuntimeError(
                f"Failed to start OpenHands container: {e.explanation or e}\n"
                f"Image: {settings.OPENHANDS_IMAGE}\n"
                f"Try: docker pull {settings.OPENHANDS_IMAGE}"
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"Failed to start OpenHands container: {e}\n"
                f"Ensure Docker Desktop is running and the OpenHands image is pulled."
            ) from e

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

                # Also check container hasn't died
                if self._container:
                    try:
                        self._container.reload()
                        if self._container.status not in ("running", "created"):
                            logs = self._container.logs(tail=30).decode(errors="replace")
                            raise RuntimeError(
                                f"OpenHands container exited ({self._container.status}).\n"
                                f"Last logs:\n{logs}"
                            )
                    except docker.errors.NotFound:
                        raise RuntimeError("OpenHands container was removed unexpectedly.")

                await asyncio.sleep(3)

        raise TimeoutError("OpenHands container did not become ready in time.")

    async def _create_conversation(self, base_url: str, task: str) -> str:
        """Create a new conversation in OpenHands and return the conversation ID."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/api/conversations",
                json={
                    "initial_user_msg": task,
                },
                timeout=30,
            )
            
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                print(f"❌ OpenHands API Error: {e.response.text}")
                raise
                
            data = resp.json()
            return data.get("conversation_id") or data.get("id") or str(data)

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
