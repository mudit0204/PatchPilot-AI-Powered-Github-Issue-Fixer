/**
 * PatchPilot Middleware Configuration
 * All environment variables and app constants in one place.
 */

import dotenv from "dotenv";
dotenv.config();

export const config = {
  // ── Server ───────────────────────────────────────────────────────────
  port: parseInt(process.env.PORT || "3001", 10),
  nodeEnv: process.env.NODE_ENV || "development",
  
  // ── URLs ─────────────────────────────────────────────────────────────
  backendUrl: process.env.BACKEND_URL || "http://localhost:8000",
  frontendUrl: process.env.FRONTEND_URL || "http://localhost:5173",
  
  // ── GitHub ───────────────────────────────────────────────────────────
  githubToken: process.env.GITHUB_TOKEN || "",
  githubWebhookSecret: process.env.GITHUB_WEBHOOK_SECRET || "",
  
  // ── SSE Configuration ────────────────────────────────────────────────
  sseTimeoutMs: parseInt(process.env.SSE_TIMEOUT_MS || "600000", 10), // 10 min default
  sseHeartbeatMs: parseInt(process.env.SSE_HEARTBEAT_MS || "15000", 10), // 15 sec
  
  // ── Rate Limiting ────────────────────────────────────────────────────
  rateLimitWindow: parseInt(process.env.RATE_LIMIT_WINDOW || "60000", 10), // 1 min
  rateLimitMax: parseInt(process.env.RATE_LIMIT_MAX || "100", 10),         // 100 req/min
  
  // ── RunStore Configuration ───────────────────────────────────────────
  maxStoredRuns: parseInt(process.env.MAX_STORED_RUNS || "100", 10),
  pruneOlderThanHours: parseInt(process.env.PRUNE_OLDER_THAN_HOURS || "24", 10),
};

// Validate critical config
if (!config.githubToken) {
  console.warn("⚠️  WARNING: GITHUB_TOKEN not set. Some features may not work.");
}

if (!config.githubWebhookSecret) {
  console.warn("⚠️  WARNING: GITHUB_WEBHOOK_SECRET not set. Webhook signature validation disabled.");
}

export default config;
