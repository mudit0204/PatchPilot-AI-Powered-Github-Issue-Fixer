/**
 * PatchPilot Backend Service
 * Centralized client for all HTTP calls to the Python FastAPI backend.
 * Uses native fetch (Node 18+) with retry logic.
 */

import { config } from "../config.js";

const BASE = config.backendUrl;

// ── Helpers ───────────────────────────────────────────────────────────────────

async function fetchJSON(path, options = {}) {
  const url = `${BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Backend error ${res.status} on ${path}: ${body}`);
  }

  return res.json();
}

// ── Issues ────────────────────────────────────────────────────────────────────

/**
 * List GitHub issues from the backend.
 * @param {string} owner
 * @param {string} repo
 * @param {object} params  - { state, per_page }
 */
export async function listIssues(owner, repo, params = {}) {
  const qs = new URLSearchParams(params).toString();
  return fetchJSON(`/api/issues/${owner}/${repo}${qs ? `?${qs}` : ""}`);
}

/**
 * Fetch a single issue.
 */
export async function getIssue(owner, repo, issueNumber) {
  return fetchJSON(`/api/issues/${owner}/${repo}/${issueNumber}`);
}

// ── Agent ─────────────────────────────────────────────────────────────────────

/**
 * Trigger an agent run on the backend.
 * Returns a Node.js ReadableStream (SSE) that the caller can pipe to the client.
 *
 * @param {object} payload - { repo_owner, repo_name, issue_number, dry_run }
 * @returns {Response}     - raw fetch Response with a streaming body
 */
export async function startAgentRun(payload) {
  const url = `${BASE}/api/agent/run`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    // Do NOT set a timeout here — SSE streams can be long-lived
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Agent run failed (${res.status}): ${body}`);
  }

  return res; // Return the raw Response so the route can pipe the SSE body
}

/**
 * Trigger a dry run (no commit/push).
 */
export async function startDryRun(payload) {
  return startAgentRun({ ...payload, dry_run: true });
}

// ── Health ────────────────────────────────────────────────────────────────────

export async function checkBackendHealth() {
  return fetchJSON("/health");
}
