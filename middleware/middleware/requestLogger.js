/**
 * PatchPilot Request Logger
 * Custom colored request/response logger middleware.
 */

/**
 * Simple request logger with colors.
 */
export function requestLogger(req, res, next) {
  const start = Date.now();
  
  // Log request
  console.log(`\n→ ${req.method} ${req.path}`);
  if (Object.keys(req.query).length > 0) {
    console.log(`  Query: ${JSON.stringify(req.query)}`);
  }
  if (req.body && Object.keys(req.body).length > 0) {
    console.log(`  Body:  ${JSON.stringify(req.body).substring(0, 100)}...`);
  }

  // Capture the original res.json to log response
  const originalJson = res.json.bind(res);
  res.json = function (body) {
    const duration = Date.now() - start;
    const statusColor = res.statusCode < 400 ? "✓" : "✗";
    
    console.log(`← ${statusColor} ${res.statusCode} ${req.method} ${req.path} (${duration}ms)`);
    
    return originalJson(body);
  };

  next();
}
