// Signed login cookie (owner, 2026-10-05: "I have to constantly login on my
// phones"). Basic Auth credentials live only as long as the browser session,
// and phones end that whenever the tab is unloaded; home-screen apps never keep
// them. A signed HttpOnly cookie lasts SESSION_DAYS on each device.
//
// Token: v1.<expiry unix seconds>.<HMAC-SHA256(user|expiry)>, keyed from
// SHARED_SECRET + the password, so changing BASIC_AUTH_PASSWORD on Vercel signs
// every device out. Nothing secret is in the cookie itself.

export const COOKIE = "rs_session";
export const SESSION_DAYS = 90;

const enc = new TextEncoder();

async function key(secret: string, pass: string) {
  return crypto.subtle.importKey("raw", enc.encode(`${secret}:${pass}`), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
}

function b64url(buf: ArrayBuffer) {
  let s = "";
  for (const b of new Uint8Array(buf)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Constant-time string compare (no early exit on the first difference). */
export function same(a: string, b: string) {
  let d = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) d |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return d === 0;
}

async function sign(user: string, exp: number, secret: string, pass: string) {
  return b64url(await crypto.subtle.sign("HMAC", await key(secret, pass), enc.encode(`${user}|${exp}`)));
}

export async function makeToken(user: string, secret: string, pass: string, now = Date.now()) {
  const exp = Math.floor(now / 1000) + SESSION_DAYS * 86400;
  return `v1.${exp}.${await sign(user, exp, secret, pass)}`;
}

export async function validToken(token: string | undefined, user: string, secret: string, pass: string, now = Date.now()) {
  if (!token) return false;
  const [v, e, sig] = token.split(".");
  const exp = Number(e);
  if (v !== "v1" || !sig || !Number.isFinite(exp) || exp * 1000 < now) return false;
  return same(sig, await sign(user, exp, secret, pass));
}
