/**
 * PatchPilot Health Route
 * Simple health check endpoint that verifies middleware and backend connectivity.
 */

import { Router } from "express";
import { checkBackendHealth } from "../services/backendService.js";
import { asyncHandler } from "../utils/asyncHandler.js";
import { RunStore } from "../services/runStore.js";

const router = Router();

router.get("/", asyncHandler(async (req, res) => {
  const components = {
    middleware: "ok",
    backend: "unknown",
  };

  // Try to reach the backend
  try {
    const backendHealth = await checkBackendHealth();
    components.backend = backendHealth.status === "healthy" ? "ok" : "degraded";
  } catch (error) {
    components.backend = "error";
    console.error("Backend health check failed:", error.message);
  }

  // Overall status
  const allOk = Object.values(components).every((status) => status === "ok");
  const status = allOk ? "ok" : "degraded";

  res.json({
    status,
    components,
    timestamp: new Date().toISOString(),
    runStore: RunStore.stats(),
  });
}));

export default router;
