/**
 * PatchPilot GitHub Webhook Service
 * Validates incoming GitHub webhook signatures and extracts issue events.
 * Used to auto-trigger PatchPilot when a label like "patchpilot" is added.
 */

import crypto from "crypto";
import { config } from "../config.js";

// ── Signature Validation ──────────────────────────────────────────────────────

/**
 * Verify the GitHub webhook HMAC-SHA256 signature.
 * GitHub sends: X-Hub-Signature-256: sha256=<hex>
 */
export function verifyWebhookSignature(rawBody, signatureHeader) {
  if (!config.githubWebhookSecret) {
    console.warn("⚠️  GITHUB_WEBHOOK_SECRET not set — skipping signature check");
    return true;
  }

  const expected = `sha256=${crypto
    .createHmac("sha256", config.githubWebhookSecret)
    .update(rawBody)
    .digest("hex")}`;

  try {
    return crypto.timingSafeEqual(
      Buffer.from(expected),
      Buffer.from(signatureHeader || "")
    );
  } catch (error) {
    console.error("Signature comparison failed:", error.message);
    return false;
  }
}

// ── Event Parsing ─────────────────────────────────────────────────────────────

/**
 * Determine if an incoming GitHub event should trigger PatchPilot.
 * Triggers when:
 *   - event = "issues"  AND  action = "labeled"
 *   - AND the label name is "patchpilot" (case-insensitive)
 *
 * @param {string} eventType  - X-GitHub-Event header value
 * @param {object} payload    - parsed JSON body
 * @returns {{ triggered: boolean, context?: object }}
 */
export function shouldTrigger(eventType, payload) {
  if (eventType !== "issues") return { triggered: false };
  if (payload.action !== "labeled") return { triggered: false };

  const labelName = payload.label?.name?.toLowerCase() || "";
  if (!["patchpilot", "patch-pilot", "auto-fix"].includes(labelName)) {
    return { triggered: false };
  }

  const issue = payload.issue;
  const repo = payload.repository;

  return {
    triggered: true,
    context: {
      repo_owner: repo.owner.login,
      repo_name: repo.name,
      issue_number: issue.number,
      issue_title: issue.title,
      issue_url: issue.html_url,
      label: payload.label.name,
    },
  };
}

// ── Ping Event ────────────────────────────────────────────────────────────────

export function isPingEvent(eventType) {
  return eventType === "ping";
}
