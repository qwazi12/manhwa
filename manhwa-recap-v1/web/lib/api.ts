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
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (alive.current) { setData(d); setError(null); }
    } catch (e: any) {
      if (alive.current) setError(e.message || String(e));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    setLoading(true);
    load();
    if (!ms) return () => { alive.current = false; };
    const t = setInterval(() => { if (!document.hidden) load(); }, ms);
    return () => { alive.current = false; clearInterval(t); };
  }, [load, ms]);
  return { data, error, loading, reload: load, setData };
}
