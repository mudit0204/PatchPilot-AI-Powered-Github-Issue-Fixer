/**
 * Async Handler Utility
 * Wraps async route handlers to automatically catch errors and pass to Express error handler.
 */

/**
 * Wraps an async function to catch errors and forward to next().
 * 
 * Usage:
 *   router.get('/path', asyncHandler(async (req, res) => {
 *     const data = await someAsyncOperation();
 *     res.json(data);
 *   }));
 */
export function asyncHandler(fn) {
  return (req, res, next) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
}
