# PatchPilot 🤖⚡

> AI-powered GitHub issue resolution · Fujitsu Project

Automatically analyze GitHub issues, generate code patches, commit fixes, and open pull requests — all driven by an AI agent powered by **Gemini** and **OpenHands**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        React Frontend                           │
│   Dashboard · Issue Viewer · Live Agent Stream · Patch Viewer  │
│                     localhost:5173                              │
└────────────────────────────┬────────────────────────────────────┘
                             │  HTTP + SSE
┌────────────────────────────▼────────────────────────────────────┐
│                    Node.js Middleware                           │
│        Express · SSE Proxy · Webhook Handler · RunStore        │
│                     localhost:3001                              │
└────────────────────────────┬────────────────────────────────────┘
                             │  HTTP + SSE
┌────────────────────────────▼────────────────────────────────────┐
│                   Python FastAPI Backend                        │
│     Orchestrator · Gemini LLM · OpenHands Docker · GitOps      │
│                     localhost:8000                              │
└────────────────────────────┬────────────────────────────────────┘
                             │  Docker SDK
┌────────────────────────────▼────────────────────────────────────┐
│               OpenHands Container (on-demand)                  │
│          Agentic code editing · File browsing · Bash           │
└─────────────────────────────────────────────────────────────────┘
                             │  GitHub API
                      GitHub Repository
```

---

## Project Structure

```
patchpilot/
├── backend/        # Python FastAPI — AI agent core
├── middleware/     # Node.js Express — API gateway
└── frontend/       # React Vite — Dashboard UI
```

---

## Quick Start

### 1. Prerequisites

- Python 3.12+
- Node.js 18+
- Docker (for OpenHands)
- GitHub Personal Access Token
- Google Gemini API key

### 2. Pull OpenHands image

```bash
docker pull ghcr.io/all-hands-ai/openhands:main
```

### 3. Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill in GEMINI_API_KEY + GITHUB_TOKEN
uvicorn main:app --reload --port 8000
```

### 4. Middleware

```bash
cd middleware
npm install
cp .env.example .env       # fill in GITHUB_TOKEN
npm run dev                # starts on port 3001
```

### 5. Frontend

```bash
cd frontend
npm install
npm run dev                # starts on port 5173
```

Open **http://localhost:5173** — enter a GitHub repo and start patching!

---

## How It Works

1. **Browse Issues** — Enter any GitHub repo in the dashboard to see open issues
2. **Trigger PatchPilot** — Click an issue and hit "Deploy PatchPilot"
3. **Watch Live** — See the AI agent's reasoning stream in real time:
   - 🧠 Thoughts — LLM reasoning steps
   - ⚡ Actions — File reads, edits, bash commands
   - 🩹 Patch — Generated unified diff
   - ✅ Commit — Git commit + push
4. **Pull Request** — A PR is automatically opened linking the issue

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Vite, CSS Modules |
| Middleware | Node.js, Express 4, SSE proxy |
| Backend | Python 3.12, FastAPI, Uvicorn |
| LLM | Google Gemini 1.5 Pro |
| Agent Runtime | OpenHands (Docker) |
| Version Control | GitHub REST API, Git CLI |

---

## Webhook Auto-Trigger

Add the label **`patchpilot`** to any GitHub issue and PatchPilot will automatically run without any manual intervention.

Setup: Repo Settings → Webhooks → `https://your-server/api/webhooks/github`

---

*Built for Fujitsu · Powered by OpenHands + Gemini*
