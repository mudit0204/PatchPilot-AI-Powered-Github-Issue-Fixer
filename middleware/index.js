/**
 * PatchPilot Middleware
 * Node.js + Express bridge between the React frontend and Python FastAPI backend.
 *
 * Responsibilities:
 *  - Serve as a single API gateway for the frontend
 *  - Forward requests to the FastAPI backend
 *  - Proxy SSE streams from FastAPI → frontend
 *  - Handle GitHub webhook events
 *  - Rate limiting, logging, error handling
 */

import express from "express";
import cors from "cors";
import helmet from "helmet";
import morgan from "morgan";
import { config } from "./config.js";

import issuesRouter from "./routes/issues.js";
import agentRouter from "./routes/agent.js";
import webhookRouter from "./routes/webhook.js";
import healthRouter from "./routes/health.js";

import { errorHandler, notFoundHandler } from "./middleware/errorHandler.js";
import { rateLimiter } from "./middleware/rateLimiter.js";
import { requestLogger } from "./middleware/requestLogger.js";

const app = express();
const PORT = config.port;

// ── Security & Parsing ────────────────────────────────────────────────────────
app.use(helmet());
app.use(
  cors({
    origin: config.frontendUrl,
    credentials: true,
  })
);
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// ── Logging ───────────────────────────────────────────────────────────────────
if (config.nodeEnv === "development") {
  app.use(morgan("dev"));
}
app.use(requestLogger);

// ── Rate Limiting ─────────────────────────────────────────────────────────────
app.use("/api/", rateLimiter);

// ── Routes ────────────────────────────────────────────────────────────────────
app.use("/health", healthRouter);
app.use("/api/issues", issuesRouter);
app.use("/api/agent", agentRouter);
app.use("/api/webhooks", webhookRouter);

// ── 404 Handler ───────────────────────────────────────────────────────────────
app.use(notFoundHandler);

// ── Global Error Handler ──────────────────────────────────────────────────────
app.use(errorHandler);

// ── Start ─────────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`\n╔══════════════════════════════════════════════════════════╗`);
  console.log(`║         PatchPilot Middleware Server                     ║`);
  console.log(`╚══════════════════════════════════════════════════════════╝`);
  console.log(`\n🚀 Server running on http://localhost:${PORT}`);
  console.log(`   → Backend:  ${config.backendUrl}`);
  console.log(`   → Frontend: ${config.frontendUrl}`);
  console.log(`   → Environment: ${config.nodeEnv}\n`);
});

export default app;
