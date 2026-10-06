"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { money } from "@/lib/fmt";
import { ConfirmButton, Meter, ToastHost, useAct } from "./ui";
import JobsBar from "./JobsBar";

type Home = {
  counts: Record<string, number>; need: any[]; n_series: number; make_next?: number;
  queue: { scheduled: number; next_post: string | null; schedule_on: boolean };
  spend: { today: number; cap: number; autopilot: number; autopilot_budget: number };
  autopilot: { enabled: boolean; per_day: number }; disk: { used_gb?: number; total_gb?: number };
  jobs: any[];
};

export const LAST_CHAPTER_KEY = "recap.lastChapter";

export default function Shell({ children }: { children: ReactNode }) {
  return (
    <ToastHost>
      <Frame>{children}</Frame>
    </ToastHost>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const path = usePathname() || "/";
  const [open, setOpen] = useState(false);
  const [last, setLast] = useState<{ id: string; title: string } | null>(null);
  const home = useApi<Home>("/api/home", 20000);
  const act = useAct();
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    try { const v = localStorage.getItem(LAST_CHAPTER_KEY); if (v) setLast(JSON.parse(v)); } catch {}
  }, [path]);
  useEffect(() => {
    try { const t = localStorage.getItem("recap.theme"); if (t) document.documentElement.dataset.theme = t; } catch {}
  }, []);
  const h = home.data;
  const c = h?.counts || {};
  const needN = h?.need?.filter((n) => n.kind !== "demand").length || 0;
  const items: [string, string, string, number | null, boolean][] = [
    ["/", "Home", "Paste a link · what needs you", needN, needN > 0],
    [last ? `/chapter/${last.id}` : "/library?view=chapters", "Board", last ? last.title : "Open a chapter to check it", null, false],
    ["/library", "Library", "Series and chapters", h?.n_series ?? null, false],
    ["/upcoming", "Upcoming", "What autopilot makes next, in order", h?.make_next || null, false],
    ["/queue", "Queue", h?.queue?.schedule_on ? `Next post ${h.queue.next_post || "—"}` : "Review, scheduled, posted",
      (c.video_ready || 0) + (h?.queue?.scheduled || 0), (c.video_ready || 0) > 0],
    ["/activity", "Activity", "Live, jobs, spend", h?.jobs?.length || null, (h?.jobs?.length || 0) > 0],
    ["/settings", "Settings", "Everything you can change", null, false],
    ["/legacy", "Legacy", "The old studio, any time", null, false],
  ];
  const isOn = (href: string) => (href === "/" ? path === "/" : href.startsWith("/chapter") || href.includes("view=chapters")
    ? path.startsWith("/chapter") : path.startsWith(href.split("?")[0].split("/").slice(0, 2).join("/")));
  return (
    <div className={`app ${open ? "open" : ""}`}>
      <div className="scrim" onClick={() => setOpen(false)} />
      <aside className="side" aria-label="Main">
        <div className="brand"><b>Recap Studio</b><span className="tag">MANHWA</span></div>
        <nav className="nav">
          {items.map(([href, t, sub, n, hot]) => (
            <Link key={t} href={href} className={isOn(href) ? "on" : ""}>
              <span className="t"><b>{t}</b><small>{sub}</small></span>
              {n ? <span className={`badge ${hot ? "hot" : ""}`}>{n}</span> : null}
            </Link>
          ))}
        </nav>
        <div className="sidefoot">
          {h && (
            <div className="spread">
              <span>Autopilot</span>
              <ConfirmButton className={`sm ${h.autopilot.enabled ? "" : "danger"}`}
                confirm={h.autopilot.enabled ? "Tap to pause" : "Tap to start"}
                title={h.autopilot.enabled ? `Running: ${h.autopilot.per_day} chapters a day` : "Paused"}
                onConfirm={() => act(async () => {
                  await api("/api/autopilot/settings", { enabled: !h.autopilot.enabled });
                  await home.reload();
                }, h.autopilot.enabled ? "Autopilot paused" : "Autopilot running")}>
                {h.autopilot.enabled ? "● Running" : "⏸ Paused"}
              </ConfirmButton>
            </div>
          )}
          {h && <Meter label="Spend today" value={h.spend.today} max={h.spend.cap} fmt={money} />}
          {h?.disk?.total_gb ? <Meter label="Disk" value={h.disk.used_gb || 0} max={h.disk.total_gb} fmt={(n) => `${n.toFixed(1)} GB`} /> : null}
          <div className="spread small">
            <a href="/storyboard" title="open the old studio directly">Old studio ↗</a>
            <button className="sm ghost" onClick={() => {
              const cur = document.documentElement.dataset.theme === "light" ? "dark" : "light";
              document.documentElement.dataset.theme = cur;
              try { localStorage.setItem("recap.theme", cur); } catch {}
            }}>◐ Theme</button>
          </div>
        </div>
      </aside>
      <div className="main">
        <div className="topbar">
          <button className="sm" onClick={() => setOpen(true)} aria-label="Open menu">☰</button>
          <b>Recap Studio</b>
        </div>
        <JobsBar onChange={home.reload} />
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
