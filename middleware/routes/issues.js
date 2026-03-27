/**
 * PatchPilot Issues Routes
 * Forwards GitHub issue requests to the FastAPI backend.
 */

import { Router } from "express";
import { listIssues, getIssue } from "../services/backendService.js";
import { asyncHandler } from "../utils/asyncHandler.js";

const router = Router();

// ── List issues ───────────────────────────────────────────────────────────────

router.get("/:owner/:repo", asyncHandler(async (req, res) => {
  const { owner, repo } = req.params;
  const { state = "open", per_page = "20" } = req.query;

  const issues = await listIssues(owner, repo, { state, per_page });
  
  res.json({
    success: true,
    data: issues,
  });
}));

// ── Get single issue ──────────────────────────────────────────────────────────

router.get("/:owner/:repo/:number", asyncHandler(async (req, res) => {
  const { owner, repo, number } = req.params;
  
  const issue = await getIssue(owner, repo, parseInt(number, 10));
  
  res.json({
    success: true,
    data: issue,
  });
}));

export default router;
