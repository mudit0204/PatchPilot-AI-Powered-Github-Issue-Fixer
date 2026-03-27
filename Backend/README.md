# PatchPilot — Backend

> AI-powered GitHub issue resolution · Python · FastAPI · Gemini · OpenHands

---

## Architecture

```
backend/
├── main.py                    # FastAPI app entry point
├── config.py                  # Settings (env vars)
├── models.py                  # Pydantic schemas
│
├── agent/
│   ├── orchestrator.py        # 🧠 Main pipeline coordinator
│   ├── llm_reasoner.py        # Gemini API integration
│   └── github_service.py      # GitHub REST API client
│
├── git_manager/
│   └── git_ops.py             # Clone / apply patch / commit / push
│
├── openhands/
│   └── runner.py              # OpenHands Docker container manager
│
└── api/
    └── routes/
        ├── health.py          # GET /health
        ├── issues.py          # GET /api/issues/{owner}/{repo}
        └── agent.py           # POST /api/agent/run  (SSE stream)
```

---

## Quickstart

### 1. Prerequisites

- Python 3.12+
- Docker (for OpenHands)
- A GitHub Personal Access Token (scopes: `repo`, `issues`, `pull_requests`)
- A Google Gemini API key

### 2. Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and fill in GEMINI_API_KEY and GITHUB_TOKEN
```

### 3. Pull OpenHands Docker Image

```bash
docker pull ghcr.io/all-hands-ai/openhands:main
```

### 4. Run the Backend

```bash
uvicorn main:app --reload --port 8000
```

API docs available at: http://localhost:8000/docs

---

## API Endpoints

### `GET /health`
Health check.

### `GET /api/issues/{owner}/{repo}`
List open GitHub issues for a repository.

Query params:
- `state` — `open` | `closed` | `all` (default: `open`)
- `per_page` — 1–100 (default: 30)

### `GET /api/issues/{owner}/{repo}/{issue_number}`
Fetch a single issue by number.

### `POST /api/agent/run`
Start the PatchPilot agent for an issue. **Returns a Server-Sent Events stream.**

Request body:
```json
{
  "repo_owner": "octocat",
  "repo_name":  "hello-world",
  "issue_number": 42,
  "branch_name": "patchpilot/fix-issue-42",   // optional
  "dry_run": false
}
```

SSE event format:
```
data: {"step_type": "thought", "content": "...", "timestamp": "...", "metadata": {}}
data: {"step_type": "action",  "content": "git clone ...", ...}
data: {"step_type": "patch",   "content": "--- a/file.py\n+++ b/file.py\n...", ...}
data: {"step_type": "commit",  "content": "✅ Committed: abc1234", ...}
data: {"step_type": "result",  "content": "🎉 PR opened: https://github.com/...", ...}

event: done
data: {}
```

### `POST /api/agent/dry-run`
Same as `/run` but skips git commit and push. Useful for previewing the patch.

---

## Pipeline Steps

1. **Fetch Issue** — Pull issue title, description, labels from GitHub
2. **Clone Repo** — Clone the target repository to local storage
3. **Build Context** — Scan file tree, read relevant source files
4. **OpenHands Agent** — Run the agentic loop inside a Docker container
5. **Gemini Fallback** — If OpenHands fails, use Gemini directly for patch generation
6. **Apply Patch** — Apply the unified diff to the local repo
7. **Commit** — Stage and commit all changes with a descriptive message
8. **Push** — Push the fix branch to GitHub
9. **Open PR** — Automatically create a Pull Request linking the issue

---

## Environment Variables

| Variable | Description | Required |
|---|---|---|
| `GEMINI_API_KEY` | Google Gemini API key | ✅ |
| `GITHUB_TOKEN` | GitHub PAT with repo/PR access | ✅ |
| `GEMINI_MODEL` | Gemini model name | ❌ (default: `gemini-1.5-pro`) |
| `OPENHANDS_IMAGE` | OpenHands Docker image | ❌ |
| `MAX_ITERATIONS` | Max agent reasoning steps | ❌ (default: 20) |
| `DEBUG` | Enable debug logging | ❌ |

---

## Docker Compose

```bash
# Build and start
docker-compose up --build

# Stop
docker-compose down
```

---

## Development Notes

- OpenHands is started **on demand** per agent run (not a persistent service)
- Each run creates an isolated Docker container that is auto-removed on completion
- The Docker socket must be mounted for OpenHands to work (see docker-compose.yml)
- Patches are stored in `/tmp/patchpilot/patches/` by default
- Cloned repos are cached in `/tmp/patchpilot/repos/` across runs
