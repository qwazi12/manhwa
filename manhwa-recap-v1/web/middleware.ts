import { NextRequest, NextResponse } from "next/server";
import { COOKIE, SESSION_DAYS, makeToken, same, validToken } from "./lib/session";

// Same gate as the old Vercel middleware (review_ui/static/middleware.js):
//  1. Sign-in with BASIC_AUTH_USER + BASIC_AUTH_PASSWORD; on production a missing one closes the site.
//     Browsers sign in on /login and get a 90-day signed cookie (lib/session.ts);
//     phones forgot the old Basic Auth popup's login on every visit (owner,
//     2026-10-05). A Basic Auth header is still accepted, for scripts.
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

// On Vercel production a missing credential must close the site, not open it
// (the old middleware silently left it open; the first draft of this one read
// the wrong variable name and would have done the same).
const PROD = process.env.VERCEL_ENV === "production";

// Harmless files a phone fetches without the login cookie (the home-screen
// manifest is requested with credentials omitted).
const OPEN = [/^\/manifest\.webmanifest$/, /^\/app-icon\//];

async function authorized(req: NextRequest): Promise<boolean> {
  const user = process.env.BASIC_AUTH_USER;
  const pass = process.env.BASIC_AUTH_PASSWORD;
  if (!user || !pass) return !PROD;
  if (await validToken(req.cookies.get(COOKIE)?.value, user, process.env.SHARED_SECRET || "", pass)) return true;
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
  return c >= 0 && same(decoded.slice(0, c), user) && same(decoded.slice(c + 1), pass);
}

/** Only same-site paths after sign-in (no "//evil.com" or full URLs). */
function safeNext(n: string | null) {
  return n && n.startsWith("/") && !n.startsWith("//") && !n.startsWith("/\\") && !n.startsWith("/login") ? n : "/";
}

const esc = (x: string) => x.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

function loginPage(next: string, error = "", status = 200) {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Sign in · Recap Studio</title><link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/app-icon/180.png">
<style>
:root{--bg:#f6f7f9;--panel:#fff;--text:#14161a;--muted:#5d6470;--border:#dde1e7;--accent:#2f6fed;--red:#c93a3a}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--panel:#181b21;--text:#e8eaee;--muted:#9aa1ad;--border:#2a2f38;--accent:#5b8cff;--red:#ff6b6b}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);font:16px/1.4 system-ui,-apple-system,sans-serif;padding:16px}
form{width:100%;max-width:360px;background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:22px;display:grid;gap:12px}
h1{font-size:20px;margin:0}p{margin:0;color:var(--muted);font-size:14px}label{display:grid;gap:4px;font-size:14px}
input{font:inherit;padding:11px 12px;border-radius:9px;border:1px solid var(--border);background:var(--bg);color:var(--text)}
button{font:inherit;font-weight:600;padding:12px;border:0;border-radius:9px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:var(--red)}
</style></head><body>
<form method="post" action="/login">
<h1>Recap Studio</h1><p>Sign in once on this device; it stays signed in for ${SESSION_DAYS} days.</p>
${error ? `<p class="err" role="alert">${esc(error)}</p>` : ""}
<label>Username<input name="user" autocomplete="username" autocapitalize="none" autocorrect="off" required autofocus></label>
<label>Password<input name="pass" type="password" autocomplete="current-password" required></label>
<input type="hidden" name="next" value="${esc(next)}">
<button type="submit">Sign in</button>
</form></body></html>`;
  return new NextResponse(html, { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}

async function login(req: NextRequest) {
  const user = process.env.BASIC_AUTH_USER || "";
  const pass = process.env.BASIC_AUTH_PASSWORD || "";
  let f: FormData;
  try { f = await req.formData(); } catch { return loginPage("/", "Something went wrong — try again.", 400); }
  const next = safeNext(String(f.get("next") || "/"));
  const ok = !!user && !!pass && same(String(f.get("user") || "").trim(), user) && same(String(f.get("pass") || ""), pass);
  if (!ok) {
    await new Promise((r) => setTimeout(r, 1000));   // slows password guessing
    return loginPage(next, "Wrong username or password.", 401);
  }
  const res = NextResponse.redirect(new URL(next, req.url), 303);
  res.cookies.set(COOKIE, await makeToken(user, process.env.SHARED_SECRET || "", pass), {
    httpOnly: true, secure: req.nextUrl.protocol === "https:", sameSite: "lax", path: "/", maxAge: SESSION_DAYS * 86400,
  });
  return res;
}

export async function middleware(req: NextRequest) {
  if (PROD && (!process.env.BASIC_AUTH_USER || !process.env.BASIC_AUTH_PASSWORD || !process.env.SHARED_SECRET)) {
    return new NextResponse("Site configuration missing: BASIC_AUTH_USER, BASIC_AUTH_PASSWORD and SHARED_SECRET must be set on Vercel.",
      { status: 503, headers: { "Cache-Control": "no-store" } });
  }
  const path = req.nextUrl.pathname;
  if (path === "/login") {
    if (req.method === "POST") return login(req);
    if (await authorized(req)) return NextResponse.redirect(new URL(safeNext(req.nextUrl.searchParams.get("next")), req.url), 303);
    return loginPage(safeNext(req.nextUrl.searchParams.get("next")));
  }
  if (path === "/logout") {
    const res = NextResponse.redirect(new URL("/login", req.url), 303);
    res.cookies.delete(COOKIE);
    return res;
  }
  const open = OPEN.some((r) => r.test(path));
  if (!open && !(await authorized(req))) {
    // Pages go to the sign-in page; API calls get a plain 401 (no browser popup).
    if (req.method === "GET" && !/^\/api\//.test(path) && (req.headers.get("accept") || "").includes("text/html")) {
      const u = new URL("/login", req.url);
      u.searchParams.set("next", path + req.nextUrl.search);
      return NextResponse.redirect(u, 303);
    }
    return NextResponse.json({ code: "unauthenticated", message: "Sign in at /login." }, { status: 401, headers: { "Cache-Control": "no-store" } });
  }
  if (PROXY.some((r) => r.test(path))) {
    const headers = new Headers(req.headers);
    if (process.env.SHARED_SECRET) headers.set("x-shared-secret", process.env.SHARED_SECRET);
    return NextResponse.rewrite(new URL(path + req.nextUrl.search, BACKEND), { request: { headers } });
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
