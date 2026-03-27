# PatchPilot Middleware

> Node.js + Express API gateway between the React frontend and Python FastAPI backend.

---

## Features

- 🔄 **SSE Proxy** — Streams real-time agent progress from FastAPI to frontend
- 🪝 **GitHub Webhooks** — Auto-triggers PatchPilot when "patchpilot" label is added
- 🛡️ **Security** — Rate limiting, CORS, Helmet, webhook signature verification
- 📊 **Run Tracking** — In-memory store for tracking agent runs (ready for Redis)
- 🔍 **Health Checks** — Monitor middleware and backend connectivity
- 📝 **Logging** — Colored request/response logs for easy debugging

---

## Folder Structure

```
middleware/
├── index.js                      # Express app entry point
├── config.js                     # All environment variables
│
├── routes/
│   ├── health.js                 # GET /health
│   ├── issues.js                 # GET /api/issues/:owner/:repo
│   ├── agent.js                  # POST /api/agent/run (SSE proxy)
│   └── webhook.js                # POST /api/webhooks/github
│
├── services/
│   ├── backendService.js         # HTTP client for FastAPI backend
│   ├── webhookService.js         # GitHub webhook validation + parsing
│   └── runStore.js               # In-memory run history store
│
├── middleware/
│   ├── errorHandler.js           # Global error handler
│   ├── rateLimiter.js            # Per-IP rate limiting
│   └── requestLogger.js          # Colored request/response logger
│
└── utils/
    └── asyncHandler.js           # Wraps async routes for error forwarding
```

---

## Quickstart

```bash
cd middleware

# Install dependencies
npm install

# Configure environment
cp .env.example .env
# Edit .env — set BACKEND_URL, GITHUB_TOKEN, etc.

# Development (auto-restart on file changes)
npm run dev

# Production
npm start
```

Middleware runs on **http://localhost:3001** by default.

---

## API Endpoints

### `GET /health`
Checks middleware + backend connectivity.

```json
{
  "status": "ok",
  "components": {
    "middleware": "ok",
    "backend": "ok"
  },
  "timestamp": "2026-03-10T...",
  "runStore": {
    "total": 5,
    "pending": 0,
    "running": 1,
    "success": 3,
    "failed": 1
  }
}
```

---

### `GET /api/issues/:owner/:repo`
List open GitHub issues.

```bash
GET /api/issues/octocat/hello-world?state=open&per_page=20
```

---

### `GET /api/issues/:owner/:repo/:number`
Fetch a single issue.

```bash
GET /api/issues/octocat/hello-world/42
```

---

### `POST /api/agent/run`
Trigger a PatchPilot agent run. Returns **SSE stream**.

**Request body:**
```json
{
  "repo_owner": "octocat",
  "repo_name": "hello-world",
  "issue_number": 42,
  "branch_name": "patchpilot/fix-42",
  "dry_run": false
}
```

**Response:** Server-Sent Events stream with agent progress updates.

---

### `POST /api/agent/dry-run`
Same as `/api/agent/run` but forces `dry_run: true`.

---

### `GET /api/agent/runs`
List all tracked agent runs.

```json
{
  "success": true,
  "data": [
    {
      "runId": "abc123",
      "status": "success",
      "request": { "repo_owner": "...", "issue_number": 42 },
      "stepCount": 15,
      "prUrl": "https://github.com/.../pull/123",
      "commitSha": "a1b2c3...",
      "startedAt": "2026-03-10T...",
      "finishedAt": "2026-03-10T..."
    }
  ],
  "count": 1
}
```

---

### `GET /api/agent/runs/:id`
Get details of a specific run including all steps.

---

### `POST /api/webhooks/github`
GitHub webhook endpoint. Listens for `issues` events with `labeled` action.

**Auto-triggers PatchPilot when:**
- Label name is one of: `patchpilot`, `patch-pilot`, or `auto-fix`

---

## GitHub Webhook Setup

1. Go to your repo → Settings → Webhooks → Add webhook
2. **Payload URL:** `https://your-domain.com/api/webhooks/github`
3. **Content type:** `application/json`
4. **Secret:** Same as `GITHUB_WEBHOOK_SECRET` in `.env`
5. **Events:** Select "Issues" only
6. Click "Add webhook"

Test it: Add the `patchpilot` label to any issue!

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Middleware server port | `3001` |
| `BACKEND_URL` | FastAPI backend URL | `http://localhost:8000` |
| `FRONTEND_URL` | React frontend URL | `http://localhost:5173` |
| `GITHUB_TOKEN` | GitHub Personal Access Token | Required |
| `GITHUB_WEBHOOK_SECRET` | Webhook signature secret | Optional |
| `SSE_TIMEOUT_MS` | Max SSE connection time | `600000` (10 min) |
| `RATE_LIMIT_MAX` | Max requests per IP per window | `100` |
| `MAX_STORED_RUNS` | Max runs to keep in memory | `100` |

---

## Production Considerations

### 1. Replace In-Memory RunStore with Redis
```javascript
// services/runStore.js
import Redis from 'ioredis';
const redis = new Redis(process.env.REDIS_URL);
```

### 2. Use PM2 for Process Management
```bash
npm install -g pm2
pm2 start index.js --name patchpilot-middleware
pm2 startup
pm2 save
```

### 3. Set Up HTTPS with Nginx
```nginx
location /api {
    proxy_pass http://localhost:3001;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection 'upgrade';
}
```

### 4. Monitor with Winston Logger
```bash
npm install winston
```

---

## Troubleshooting

### SSE connections timing out?
- Increase `SSE_TIMEOUT_MS` in `.env`
- Check if proxy/load balancer supports long-lived connections

### Webhook signature verification failing?
- Ensure `GITHUB_WEBHOOK_SECRET` matches GitHub webhook settings
- Check that raw body is being preserved (avoid body-parser middleware)

### Rate limiting too aggressive?
- Adjust `RATE_LIMIT_WINDOW` and `RATE_LIMIT_MAX` in `.env`
- Consider implementing Redis-based rate limiting for multi-instance setups

---

## License

MIT
