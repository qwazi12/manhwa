import { NextRequest, NextResponse } from "next/server";

// Same gate as the old Vercel middleware (review_ui/static/middleware.js):
//  1. Basic Auth, when BASIC_AUTH_USER and BASIC_AUTH_PASS are both set.
//  2. Requests for the backend are forwarded to Railway with SHARED_SECRET as
//     x-shared-secret. The secret lives only in Vercel's env; the browser never
//     sees it, so the backend stays unreachable directly.
// The classic pages (/storyboard, /review) are forwarded too, so they keep
// working while the new pages replace them.

const BACKEND = process.env.BACKEND_URL || "https://recap-studio-production.up.railway.app";
const PROXY = [
  /^\/api\//, /^\/clip\//, /^\/thumb\//, /^\/thumbnail$/, /^\/thumbconcept$/, /^\/audio\//,
  /^\/panelimg\//, /^\/splitimg\//, /^\/segimg\//, /^\/export\//, /^\/storyboard$/, /^\/review$/,
  /^\/manifest\.webmanifest$/, /^\/app-icon\//,
];

function authorized(req: NextRequest): boolean {
  const user = process.env.BASIC_AUTH_USER;
  const pass = process.env.BASIC_AUTH_PASS;
  if (!user || !pass) return true;
  const h = req.headers.get("authorization") || "";
  const i = h.indexOf(" ");
  if (i < 0 || h.slice(0, i).toLowerCase() !== "basic") return false;
  let decoded = "";
  try {
    decoded = atob(h.slice(i + 1));
  } catch {
    return false;
  }
  const c = decoded.indexOf(":");
  return c >= 0 && decoded.slice(0, c) === user && decoded.slice(c + 1) === pass;
}

export function middleware(req: NextRequest) {
  if (!authorized(req)) {
    return new NextResponse("Authentication required.", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="Manhwa Recap Studio", charset="UTF-8"', "Cache-Control": "no-store" },
    });
  }
  const path = req.nextUrl.pathname;
  if (PROXY.some((r) => r.test(path))) {
    const headers = new Headers(req.headers);
    if (process.env.SHARED_SECRET) headers.set("x-shared-secret", process.env.SHARED_SECRET);
    return NextResponse.rewrite(new URL(path + req.nextUrl.search, BACKEND), { request: { headers } });
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
