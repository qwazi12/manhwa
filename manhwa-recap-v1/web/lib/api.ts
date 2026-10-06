"use client";
import { useCallback, useEffect, useRef, useState } from "react";

/** One way to talk to the backend: same-origin paths, forwarded by middleware.ts. */
export async function api<T = any>(path: string, body?: unknown, method?: string): Promise<T> {
  const init: RequestInit = { method: method || (body !== undefined ? "POST" : "GET"), cache: "no-store" };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const r = await fetch(path, init);
  if (r.status === 401 && typeof location !== "undefined" && !location.pathname.startsWith("/login")) {
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);   // sign-in expired
  }
  if (!r.ok) {
    let msg = await r.text();
    try {
      const j = JSON.parse(msg);
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {}
    throw new Error(msg || `${r.status} ${r.statusText}`);
  }
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("json") ? r.json() : (r.text() as any)) as Promise<T>;
}

/** Load (and optionally re-load every `ms`) one endpoint. Pauses while the tab is hidden. */
export function useApi<T = any>(path: string | null, ms = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  // Only the answer for the CURRENT path may land: a slow reply for the page
  // you just left (e.g. the previous chapter) used to arrive late and replace
  // the new one's data (owner, 2026-10-05: Murim's board, Iron-Blooded's video).
  const current = useRef(path);
  current.current = path;
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (alive.current && current.current === path) { setData(d); setError(null); }
    } catch (e: any) {
      if (alive.current && current.current === path) setError(e.message || String(e));
    } finally {
      if (alive.current && current.current === path) setLoading(false);
    }
  }, [path]);
  const first = useRef(true);
  useEffect(() => {
    alive.current = true;
    if (!first.current) setData(null);      // never show the previous path's data
    first.current = false;
    setLoading(true);
    load();
    if (!ms) return () => { alive.current = false; };
    const t = setInterval(() => { if (!document.hidden) load(); }, ms);
    return () => { alive.current = false; clearInterval(t); };
  }, [load, ms]);
  return { data, error, loading, reload: load, setData };
}
