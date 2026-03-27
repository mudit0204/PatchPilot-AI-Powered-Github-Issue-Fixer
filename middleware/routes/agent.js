/**
 * PatchPilot Agent Routes
 *
 * POST /api/agent/run        — trigger a fix run (SSE stream)
 * POST /api/agent/dry-run    — dry run (no commit/push)
 * GET  /api/agent/runs       — list all runs (from in-memory store)
 * GET  /api/agent/runs/:id   — get a specific run
 */

import { Router } from "express";
import { v4 as uuid } from "uuid";
import { startAgentRun, startDryRun } from "../services/backendService.js";
import { RunStore } from "../services/runStore.js";
import { asyncHandler } from "../utils/asyncHandler.js";
import { config } from "../config.js";

const router = Router();

// ── Trigger a fix run (SSE) ───────────────────────────────────────────────────

router.post("/run", asyncHandler(async (req, res) => {
  const { repo_owner, repo_name, issue_number, branch_name, dry_run = false } = req.body;

  if (!repo_owner || !repo_name || !issue_number) {
    return res.status(400).json({
      success: false,
      error: "repo_owner, repo_name, and issue_number are required.",
    });
  }

  const runId = uuid();
  const payload = { repo_owner, repo_name, issue_number, branch_name, dry_run };

  // Record run in store
  RunStore.create(runId, payload);
  RunStore.update(runId, { status: "running" });

  // ── Set up SSE headers ────────────────────────────────────────────
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  // Emit the run ID immediately so the frontend can track this run
  res.write(`data: ${JSON.stringify({ event: "run_started", run_id: runId })}\n\n`);

  // ── Heartbeat — keeps connection alive through proxies ─────────────
  const heartbeat = setInterval(() => {
    res.write(": heartbeat\n\n");
  }, config.sseHeartbeatMs);

  // ── Set overall SSE timeout ────────────────────────────────────────
  const timeout = setTimeout(() => {
    res.write(`data: ${JSON.stringify({ step_type: "error", content: "Agent run timed out." })}\n\n`);
    res.write("event: done\ndata: {}\n\n");
    res.end();
  }, config.sseTimeoutMs);

  try {
    // Get streaming response from FastAPI
    const backendRes = await (dry_run ? startDryRun(payload) : startAgentRun(payload));

    // Pipe FastAPI SSE → client SSE, accumulating steps
    const reader = backendRes.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // Keep incomplete last line in buffer

      for (const line of lines) {
        if (!line.trim()) continue;

        // Forward raw SSE line to client
        res.write(line + "\n");

        // Parse and store steps
        if (line.startsWith("data: ")) {
          try {
            const step = JSON.parse(line.slice(6));
            RunStore.addStep(runId, step);

            // Extract PR URL and commit SHA from final result step
            if (step.metadata?.pr_url) RunStore.update(runId, { prUrl: step.metadata.pr_url });
            if (step.metadata?.commit_sha) RunStore.update(runId, { commitSha: step.metadata.commit_sha });
          } catch (_) {
            /* ignore parse errors on non-JSON lines */
          }
        }

        // Detect end-of-stream from backend
        if (line === "event: done") {
          RunStore.markDone(runId);
          res.write("\nevent: done\ndata: {}\n\n");
          break;
        }
      }
    }
  } catch (err) {
    console.error("Agent run error:", err.message);
    RunStore.markFailed(runId, err.message);
    res.write(`data: ${JSON.stringify({ step_type: "error", content: err.message })}\n\n`);
    res.write("event: done\ndata: {}\n\n");
  } finally {
    clearInterval(heartbeat);
    clearTimeout(timeout);
    RunStore.prune();
    res.end();
  }
}));

// ── Dry run shortcut ──────────────────────────────────────────────────────────

router.post("/dry-run", asyncHandler(async (req, res, next) => {
  req.body.dry_run = true;
  // Forward to /run handler
  const runHandler = router.stack.find(layer => layer.route?.path === "/run" && layer.route?.methods.post);
  if (runHandler) {
    return runHandler.route.stack[0].handle(req, res, next);
  }
  next(new Error("Run handler not found"));
}));

// ── List all runs ─────────────────────────────────────────────────────────────

router.get("/runs", (req, res) => {
  const runs = RunStore.list().map((r) => ({
    runId: r.runId,
    status: r.status,
    request: r.request,
    stepCount: r.steps.length,
    prUrl: r.prUrl,
    commitSha: r.commitSha,
    startedAt: r.startedAt,
    finishedAt: r.finishedAt,
  }));

  res.json({ success: true, data: runs, count: runs.length });
});

// ── Get a specific run ────────────────────────────────────────────────────────

router.get("/runs/:id", (req, res) => {
  const run = RunStore.get(req.params.id);
  if (!run) return res.status(404).json({ success: false, error: "Run not found." });
  res.json({ success: true, data: run });
});

export default router;
