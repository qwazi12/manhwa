"use client";
import { api, useApi } from "@/lib/api";
import { ago, when } from "@/lib/fmt";
import { Card, useAct } from "@/components/ui";

// Owner, 2026-11-18: "a monitor for new chapters that runs constantly and is
// consistent". The state, when it last ran, when it runs next, how to stop it.
export default function MonitorStrip() {
  const { data: m, reload } = useApi<any>("/api/monitor", 30000);
  const act = useAct();
  if (!m) return null;
  const lp = m.last_pass;
  const waiting: any[] = m.waiting || [];
  const failing: any[] = m.failing || [];
  return (
    <Card title="New-chapter check"
      right={<div className="row">
        <button className="sm" disabled={m.running} onClick={() => act(async () => { await api("/api/monitor/run", {}); setTimeout(reload, 4000); }, "Checking every series now…")}>
          {m.running ? "Checking…" : "Check now"}</button>
        <button className="sm" onClick={() => act(async () => { await api("/api/monitor", { enabled: !m.enabled }); await reload(); })}>
          {m.enabled ? "Turn off" : "Turn on"}</button>
      </div>}>
      <div className="small" style={{ display: "grid", gap: 6 }}>
        <span>
          <b style={{ color: m.enabled ? "var(--accent)" : "var(--yellow)" }}>{m.enabled ? "On" : "Off"}</b>
          {m.enabled && <> · every {m.every_minutes} min</>}
          {lp?.ended ? <> · last check {ago(lp.ended)} ({lp.checked} series{lp.failed ? `, ${lp.failed} could not be read` : ""}{lp.new ? `, ${lp.new} new` : ""})</> : <> · no check yet</>}
          {m.enabled && m.next_due ? <> · next {m.running ? "running now" : when(m.next_due)}</> : null}
        </span>
        {m.late && <span style={{ color: "var(--red)" }}>⚠ The check is late. It restarts by itself within 10 minutes; if this stays, tell the developer.</span>}
        {waiting.length > 0 && <span><b>Found, not made yet:</b> {waiting.slice(0, 12).map((f) => `${f.title} ch.${f.chapter} (${ago(f.at)})`).join(" · ")}</span>}
        {failing.length > 0 && <span style={{ color: (m.stuck || []).length ? "var(--red)" : "var(--yellow)" }}>
          Could not be read: {failing.slice(0, 8).map((s) => `${s.title}${s.fails > 1 ? ` (${s.fails} checks in a row)` : ""}`).join(" · ")}</span>}
      </div>
    </Card>
  );
}
