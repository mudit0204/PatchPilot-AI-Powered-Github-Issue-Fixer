/**
 * PatchPilot GitHub Webhook Routes
 * Receives and processes GitHub webhook events.
 * Auto-triggers PatchPilot when the "patchpilot" label is added to an issue.
 */

import express, { Router } from "express";
import { verifyWebhookSignature, shouldTrigger, isPingEvent } from "../services/webhookService.js";
import { startAgentRun } from "../services/backendService.js";
import { asyncHandler } from "../utils/asyncHandler.js";

const router = Router();

// ── GitHub Webhook Endpoint ───────────────────────────────────────────────────

router.post("/github", express.raw({ type: "application/json" }), asyncHandler(async (req, res) => {
  const signature = req.headers["x-hub-signature-256"];
  const eventType = req.headers["x-github-event"];
  const deliveryId = req.headers["x-github-delivery"];

  console.log(`\n📥 GitHub Webhook received: ${eventType} (${deliveryId})`);

  // ── Verify signature ──────────────────────────────────────────────
  const rawBody = req.body.toString("utf-8");
  if (!verifyWebhookSignature(rawBody, signature)) {
    console.error("❌ Webhook signature verification failed");
    return res.status(401).json({ success: false, error: "Invalid signature" });
  }

  // ── Parse payload ─────────────────────────────────────────────────
  let payload;
  try {
    payload = JSON.parse(rawBody);
  } catch (error) {
    console.error("❌ Failed to parse webhook payload");
    return res.status(400).json({ success: false, error: "Invalid JSON payload" });
  }

  // ── Handle ping event ─────────────────────────────────────────────
  if (isPingEvent(eventType)) {
    console.log("✅ Webhook ping received — configuration successful!");
    return res.json({
      success: true,
      message: "Pong! Webhook is configured correctly.",
      zen: payload.zen,
    });
  }

  // ── Check if we should trigger PatchPilot ─────────────────────────
  const { triggered, context } = shouldTrigger(eventType, payload);

  if (!triggered) {
    console.log(`ℹ️  Event ${eventType} did not trigger PatchPilot`);
    return res.json({
      success: true,
      message: "Event received but not actionable",
      eventType,
    });
  }

  // ── Trigger PatchPilot ────────────────────────────────────────────
  console.log(`🚀 Auto-triggering PatchPilot for issue #${context.issue_number}`);
  console.log(`   Repo:  ${context.repo_owner}/${context.repo_name}`);
  console.log(`   Label: ${context.label}`);

  // Start agent run asynchronously (don't wait for completion)
  startAgentRun({
    repo_owner: context.repo_owner,
    repo_name: context.repo_name,
    issue_number: context.issue_number,
    branch_name: `patchpilot/fix-${context.issue_number}`,
    dry_run: false,
  }).catch((error) => {
    console.error(`❌ Failed to start agent run: ${error.message}`);
  });

  res.json({
    success: true,
    message: "PatchPilot triggered",
    context,
  });
}));

export default router;
