/**
 * PatchPilot Run Store
 * In-memory storage for tracking agent runs and their progress.
 * In production, consider replacing this with Redis or a database.
 */

import { config } from "../config.js";

class RunStoreClass {
  constructor() {
    this.runs = new Map(); // runId → run object
  }

  /**
   * Create a new run entry.
   * @param {string} runId
   * @param {object} request - The original request payload
   */
  create(runId, request) {
    this.runs.set(runId, {
      runId,
      status: "pending",
      request,
      steps: [],
      prUrl: null,
      commitSha: null,
      startedAt: new Date().toISOString(),
      finishedAt: null,
      error: null,
    });
    return this.runs.get(runId);
  }

  /**
   * Get a run by ID.
   */
  get(runId) {
    return this.runs.get(runId) || null;
  }

  /**
   * Update run metadata.
   */
  update(runId, updates) {
    const run = this.runs.get(runId);
    if (!run) return;

    Object.assign(run, updates);
  }

  /**
   * Add a step to the run's execution log.
   */
  addStep(runId, step) {
    const run = this.runs.get(runId);
    if (!run) return;

    run.steps.push({
      ...step,
      timestamp: step.timestamp || new Date().toISOString(),
    });
  }

  /**
   * Mark a run as done (success).
   */
  markDone(runId) {
    this.update(runId, {
      status: "success",
      finishedAt: new Date().toISOString(),
    });
  }

  /**
   * Mark a run as failed.
   */
  markFailed(runId, error) {
    this.update(runId, {
      status: "failed",
      error,
      finishedAt: new Date().toISOString(),
    });
  }

  /**
   * List all runs (newest first).
   */
  list() {
    return Array.from(this.runs.values()).sort(
      (a, b) => new Date(b.startedAt) - new Date(a.startedAt)
    );
  }

  /**
   * Prune old runs to prevent memory bloat.
   * Keeps only the most recent MAX_STORED_RUNS and deletes those older than PRUNE_OLDER_THAN_HOURS.
   */
  prune() {
    const allRuns = this.list();
    
    // Keep only the most recent runs
    if (allRuns.length > config.maxStoredRuns) {
      const toDelete = allRuns.slice(config.maxStoredRuns);
      toDelete.forEach((run) => this.runs.delete(run.runId));
    }

    // Delete runs older than configured threshold
    const cutoff = Date.now() - config.pruneOlderThanHours * 60 * 60 * 1000;
    for (const [runId, run] of this.runs.entries()) {
      if (new Date(run.startedAt).getTime() < cutoff) {
        this.runs.delete(runId);
      }
    }
  }

  /**
   * Clear all runs (for testing).
   */
  clear() {
    this.runs.clear();
  }

  /**
   * Get basic stats.
   */
  stats() {
    const runs = this.list();
    return {
      total: runs.length,
      pending: runs.filter((r) => r.status === "pending").length,
      running: runs.filter((r) => r.status === "running").length,
      success: runs.filter((r) => r.status === "success").length,
      failed: runs.filter((r) => r.status === "failed").length,
    };
  }
}

// Export a singleton instance
export const RunStore = new RunStoreClass();
