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

    @staticmethod
    def _is_running_in_container() -> bool:
        """Best-effort detection for code running inside a Docker container."""
        return os.path.exists("/.dockerenv")

    def _api_base_url(self) -> str:
        """Resolve OpenHands API base URL for host vs in-container execution."""
        if self._host_port is None:
            raise RuntimeError("OpenHands host port is not initialized.")

        host = "host.docker.internal" if self._is_running_in_container() else "localhost"
        return f"http://{host}:{self._host_port}"

    def _get_volumes(self, workspace_path: str) -> dict:
        """Build volume mounts, handling Windows vs Linux Docker socket."""
        docker_workspace_path = self._docker_visible_workspace_path(workspace_path)
        volumes = {
            docker_workspace_path: {"bind": settings.OPENHANDS_WORKSPACE, "mode": "rw"},
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

    @staticmethod
    def _docker_visible_workspace_path(workspace_path: str) -> str:
        """
        Convert an in-container /app path to the equivalent host path.

        The backend itself runs in Docker, but it talks to the host Docker
        daemon. Bind mounts for the OpenHands container therefore need host
        paths, not the backend container's /app/... paths.
        """
        path = os.path.abspath(workspace_path)
        host_root = (settings.PATCHPILOT_HOST_ROOT or "").strip()

        if OpenHandsRunner._is_running_in_container() and host_root and path.startswith("/app"):
            rel = os.path.relpath(path, "/app")
            return os.path.join(host_root, rel).replace("\\", "/")

        return path

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
        base_url = self._api_base_url()

        # Wait for container to be ready and initialize settings for this runtime.
        await self._wait_for_ready(base_url)
        await self._ensure_settings(base_url)

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

        readiness_paths = (
            "/api/options/models",
            "/api/options/models/",
            "/api/settings",
            "/",
        )

        async with httpx.AsyncClient() as client:
            while asyncio.get_event_loop().time() < deadline:
                for path in readiness_paths:
                    try:
                        resp = await client.get(f"{base_url}{path}", timeout=5, follow_redirects=True)
                        if resp.status_code < 500:
                            print(f"✅ OpenHands is ready via {path}!")
                            return
                    except Exception:
                        continue

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

    async def _settings_payload(self) -> dict:
        """Build settings payload required by newer OpenHands APIs."""
        provider = self._normalize_provider()
        payload = {
            "llm_model": self._openhands_model_name(),
            "llm_api_key": "ollama" if provider == "ollama" else settings.GEMINI_API_KEY,
        }

        if provider == "ollama":
            payload["llm_base_url"] = settings.OLLAMA_BASE_URL_DOCKER

        return payload

    async def _ensure_settings(self, base_url: str):
        """Initialize server-side OpenHands settings before starting conversations."""
        payload = await self._settings_payload()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/api/settings",
                json=payload,
                timeout=30,
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                print(f"❌ OpenHands settings error: {e.response.text}")
                raise

    async def _create_conversation(self, base_url: str, task: str) -> str:
        """Create a new conversation in OpenHands and return the conversation ID."""
        async with httpx.AsyncClient() as client:
            url = f"{base_url}/api/conversations"

            # Primary payload (matches newer OpenHands schemas)
            payloads = [
                {"initial_user_msg": task},
                {"initial_message": task},
                {"messages": [{"role": "user", "content": task}]},
            ]

            last_exc = None
            for idx, payload in enumerate(payloads, start=1):
                try:
                    resp = await client.post(url, json=payload, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    return data.get("conversation_id") or data.get("id") or str(data)
                except httpx.HTTPStatusError as e:
                    body = e.response.text
                    code = e.response.status_code
                    print(f"❌ OpenHands API Error (attempt {idx}) {code}: {body}")
                    last_exc = e
                    # If 400, try the next payload; otherwise surface immediately
                    if code != 400:
                        raise
                except Exception as e:
                    print(f"❌ OpenHands request failed (attempt {idx}): {e}")
                    last_exc = e

            # If we reach here, all payload attempts failed — raise the last exception with context
            if last_exc:
                raise RuntimeError(
                    f"Failed to create OpenHands conversation after trying multiple payloads. Last error: {last_exc}"
                ) from last_exc

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
