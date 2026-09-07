import { next } from '@vercel/edge';

// PUBLIC ACCESS — the Basic Auth gate was removed at the owner's request.
//
// This middleware still runs on every request for one essential reason: it
// injects SHARED_SECRET as the x-shared-secret header. The Railway backend
// rejects /api, /clip, /thumb, /audio, /panelimg and /export without it
// (server.py verify_shared_secret). That secret lives only in Vercel's edge
// env and is never sent to the browser, so the backend stays unreachable
// directly — but anyone who knows this site's URL can now use the board,
// including its destructive controls (delete, approve/render, ingest).
// Re-gating is a matter of restoring the auth check here and redeploying.
export function middleware(req) {
  const requestHeaders = new Headers(req.headers);
  if (process.env.SHARED_SECRET) {
    requestHeaders.set('x-shared-secret', process.env.SHARED_SECRET);
  }
  return next({
    request: {
      headers: requestHeaders,
    },
  });
}

export default middleware;

export const config = {
  matcher: ['/:path*'],
};
