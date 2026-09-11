import { next } from '@vercel/edge';

// GATED ACCESS. This middleware does two jobs on every request:
//
//  1. Basic Auth. The board's controls are destructive (delete a project,
//     approve/render which spends Gemini + TTS credit, ingest a chapter), and
//     the publish work now landing makes an unauthenticated site untenable.
//
//  2. Injects SHARED_SECRET as x-shared-secret. The Railway backend rejects
//     /api, /clip, /thumb, /audio, /panelimg, /segimg and /export without it
//     (server.py verify_shared_secret). That secret lives only in Vercel's edge
//     env and is never sent to the browser, so the backend stays unreachable
//     directly.
//
// BOTH paths must inject the secret. A previous version returned early on one
// branch without injecting, which loaded the page shell while every image and
// API call 401'd — the failure looked like a broken app rather than a config
// problem. Hence the single exit below.
//
// The gate engages ONLY when both credentials are configured. Enforcing with
// nothing set would lock the owner out of their own tool with no way back in;
// the trade is that a missing env var silently leaves the site open, so verify
// after deploying rather than assuming.

const USER = process.env.BASIC_AUTH_USER;
const PASS = process.env.BASIC_AUTH_PASSWORD;

function unauthorized() {
  return new Response('Authentication required.', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="Manhwa Recap Studio", charset="UTF-8"',
      'Cache-Control': 'no-store',
    },
  });
}

function credentialsOk(req) {
  const header = req.headers.get('authorization') || '';
  const space = header.indexOf(' ');
  if (space < 0) return false;
  if (header.slice(0, space).toLowerCase() !== 'basic') return false;
  let decoded;
  try {
    decoded = atob(header.slice(space + 1));
  } catch (e) {
    return false;
  }
  const colon = decoded.indexOf(':');
  if (colon < 0) return false;
  return decoded.slice(0, colon) === USER && decoded.slice(colon + 1) === PASS;
}

export function middleware(req) {
  if (USER && PASS && !credentialsOk(req)) {
    return unauthorized();
  }
  const requestHeaders = new Headers(req.headers);
  if (process.env.SHARED_SECRET) {
    requestHeaders.set('x-shared-secret', process.env.SHARED_SECRET);
  }
  return next({ request: { headers: requestHeaders } });
}

export default middleware;

export const config = {
  matcher: ['/:path*'],
};
