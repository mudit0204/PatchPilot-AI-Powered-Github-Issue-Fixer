/**
 * PatchPilot Error Handler
 * Global catch-all error middleware for Express.
 * Logs errors and returns consistent JSON error responses.
 */

/**
 * Express error handling middleware.
 * Must have 4 parameters (err, req, res, next) for Express to recognize it as an error handler.
 */
export function errorHandler(err, req, res, next) {
  // Log the error
  console.error("\n❌ Error caught by errorHandler:");
  console.error(`   Route:  ${req.method} ${req.path}`);
  console.error(`   Error:  ${err.message}`);
  if (err.stack) {
    console.error(`   Stack:  ${err.stack.split("\n").slice(0, 3).join("\n")}`);
  }

  // Determine status code
  const statusCode = err.statusCode || err.status || 500;

  // Send JSON error response
  res.status(statusCode).json({
    success: false,
    error: err.message || "Internal server error",
    ...(process.env.NODE_ENV === "development" && {
      stack: err.stack,
      details: err.details,
    }),
  });
}

/**
 * 404 handler — should be placed AFTER all route definitions.
 */
export function notFoundHandler(req, res) {
  res.status(404).json({
    success: false,
    error: `Route not found: ${req.method} ${req.path}`,
  });
}
