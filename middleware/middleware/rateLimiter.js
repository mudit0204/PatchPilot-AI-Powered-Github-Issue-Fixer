/**
 * PatchPilot Rate Limiter
 * Simple in-memory rate limiting per IP address.
 * For production, consider using express-rate-limit or Redis-backed rate limiting.
 */

import { config } from "../config.js";

// In-memory store: IP → { count, resetAt }
const store = new Map();

/**
 * Rate limiting middleware.
 * Limits requests per IP within a time window.
 */
export function rateLimiter(req, res, next) {
  const ip = req.ip || req.connection.remoteAddress;
  const now = Date.now();

  // Get or create record for this IP
  let record = store.get(ip);

  if (!record || now > record.resetAt) {
    // First request or window expired — reset
    record = {
      count: 1,
      resetAt: now + config.rateLimitWindow,
    };
    store.set(ip, record);
    return next();
  }

  // Increment count
  record.count++;

  if (record.count > config.rateLimitMax) {
    const retryAfter = Math.ceil((record.resetAt - now) / 1000);
    res.set("Retry-After", retryAfter);
    return res.status(429).json({
      success: false,
      error: "Too many requests. Please try again later.",
      retryAfter: `${retryAfter}s`,
    });
  }

  next();
}

/**
 * Cleanup old entries periodically to prevent memory leak.
 * Call this in a setInterval or via a cron job.
 */
export function cleanupRateLimitStore() {
  const now = Date.now();
  for (const [ip, record] of store.entries()) {
    if (now > record.resetAt) {
      store.delete(ip);
    }
  }
}

// Auto-cleanup every 5 minutes
setInterval(cleanupRateLimitStore, 5 * 60 * 1000);
