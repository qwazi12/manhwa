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


export interface ReleaseMomentum {
  has_release_date: boolean;
  release_date: string | null;
  days_until_release: number | null;
  status: string;
  urgency: string;
  is_pre_release: boolean;
  is_post_release: boolean;
  scheduled_after_release: boolean;
  badge_text: string;
  badge_variant: 'critical' | 'approaching' | 'upcoming' | 'missed' | 'neutral';
  warning?: string | null;
  priority_score: number;
}

export interface StudioProject {
  id: number;
  project_id: string;
  series_slug: string;
  chapter_number: number;
  title: string;
  target_minutes: number;
  stage: string;
  stage_status: 'idle' | 'running' | 'queued' | 'done' | 'error' | 'stopped' | 'paused';
  stage_message?: string | null;
  poster?: string | null;
  momentum?: ReleaseMomentum | null;
  has: {
    scrape: boolean;
    split: boolean;
    describe: boolean;
    narrate: boolean;
    voice: boolean;
    match: boolean;
    render: boolean;
  };
  facts?: any;
  research?: any;
  render?: any;
  queue_item_id?: number | null;
  archive?: any;
  cost_usd: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface UndoStep {
  id: number;
  label: string;
  created_at: string | null;
}

export interface ScheduleConfig {
  timezone: string;
  start_hour: number;
  end_hour: number;
  interval_hours: number;
  posts_per_day: number | null;
  pipeline_overrides?: Record<string, any>;
  account_overrides?: Record<string, any>;
}

export interface ScheduleInfo extends ScheduleConfig {
  slots_per_day: number;
  total_ready: number;
  runway_days: number;
  runway_label: string;
  next_slots: string[];
}

export const studioApi = {
  projects: () => api<StudioProject[]>("/api/studio/projects"),
  project: (id: string) => api<StudioProject>("/api/studio/projects/" + encodeURIComponent(id)),
  run: (projectId: string, stage: string, auto = false, until = "match") =>
    api<{ ok: boolean; job_id: string }>("/api/studio/run", { project_id: projectId, stage, auto, until }),
  stop: (projectId: string) =>
    api<{ ok: boolean }>("/api/studio/stop", { project_id: projectId }),
  jobs: () => api<any[]>("/api/control/jobs"),
  stopJob: (id: string) => api<{ ok: boolean }>("/api/control/jobs/" + encodeURIComponent(id) + "/stop", {}),
  schedule: () => api<ScheduleInfo>("/api/schedule"),
  saveSchedule: (cfg: any) => api<{ ok: boolean; schedule: any }>("/api/schedule", cfg),
  undoStack: (scope: string) => api<{ scope: string; stack: UndoStep[] }>("/api/undo/" + encodeURIComponent(scope) + "/stack"),
  undo: (scope: string) => api<{ ok: boolean; undone: string; stack: UndoStep[] }>("/api/undo/" + encodeURIComponent(scope), {}),
  space: () => api<any>("/api/space"),
  backupStatus: () => api<any>("/api/backup/status"),
  runBackup: () => api<any>("/api/backup/run", {}),
};
