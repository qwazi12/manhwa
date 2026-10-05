"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago } from "@/lib/fmt";
import { Card, Chips, ConfirmButton, Empty, StatusPill, useAct, useToast } from "./ui";

/** Every chapter in one table: filter by status, select, delete in bulk.
 *  Replaces the classic Projects page. */
export default function AllChapters() {
  const { data, reload } = useApi<any>("/api/chapters", 20000);
  const act = useAct();
  const toast = useToast();
  const [f, setF] = useState("all");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<string[]>([]);
  const rows: any[] = data?.chapters || [];
  const counts: Record<string, number> = data?.counts || {};
  const shown = useMemo(() => rows.filter((r) =>
    (f === "all" ? r.status.key !== "archived" : r.status.key === f) &&
    (!q || (r.title || "").toLowerCase().includes(q.toLowerCase()))), [rows, f, q]);
  const keys = (data?.statuses || []).filter((s: any) => counts[s.key]);
  const allOn = shown.length > 0 && shown.every((r) => sel.includes(r.id));
  return (
    <Card pad={false}
      title={<Chips value={f} onChange={(v) => { setF(v); setSel([]); }}
        items={[["all", `All (${rows.filter((r) => r.status.key !== "archived").length})`],
          ...keys.map((s: any) => [s.key, `${s.label} (${counts[s.key]})`] as [string, string])]} />}
      right={<input placeholder="Search chapters" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 220 }} aria-label="Search chapters" />}>
      <div className="spread" style={{ padding: "8px 14px", borderBottom: "1px solid var(--border)" }}>
        <label className="check small"><input type="checkbox" checked={allOn}
          onChange={(e) => setSel(e.target.checked ? shown.map((r) => r.id) : [])} /> Select all shown</label>
        <span className="row">
        <button className="sm primary" disabled={!sel.length} title="Renders them one after another, each in its own voice (free). Progress shows in the bar at the top."
          onClick={() => act(async () => {
            const r = await api("/api/render-queue/add", { projects: sel });
            setSel([]); reload();
            toast(`${r.added.length} added to the render queue — progress shows at the top` +
              (r.skipped.length ? ` · not added: ${r.skipped.map((x: any) => `${x.name} (${x.reason})`).join("; ")}` : ""), r.skipped.length > 0 && !r.added.length);
          })}>🎬 Render selected ({sel.length})</button>
        <ConfirmButton className="sm danger" disabled={!sel.length} confirm={`Delete ${sel.length}? This can’t be undone`}
          onConfirm={() => act(async () => {
            const r = await api("/api/projects/delete", { ids: sel });
            setSel([]); reload();
            toast(`Deleted ${r.deleted.length}, freed ${r.freed_mb} MB` +
              (r.skipped.length ? ` · not deleted: ${r.skipped.map((x: any) => `${x.id} (${x.reason})`).join("; ")}` : ""), r.skipped.length > 0);
          })}>
          🗑 Delete selected ({sel.length})
        </ConfirmButton>
        </span>
      </div>
      <div className="list">
        {!data ? <Empty>Loading…</Empty> : shown.length === 0 ? <Empty>No chapters here.</Empty> : shown.map((r) => (
          <div className="it" key={r.id}>
            <div className="row" style={{ minWidth: 0, flex: 1 }}>
              <input type="checkbox" checked={sel.includes(r.id)} aria-label={`Select ${r.title}`}
                onChange={(e) => setSel(e.target.checked ? [...sel, r.id] : sel.filter((x) => x !== r.id))} />
              <StatusPill s={r.status} />
              <b>{r.title}</b>
              {r.auto && <span className="small faint">autopilot</span>}
              {r.engine === "claude" && <span className="small faint">Claude lab</span>}
              {r.archive && <span className="small faint">{r.archive.keep ? "kept" : `deleted in ${r.archive.days_left} day(s)`}</span>}
              <span className="small faint">{ago(r.updated)}</span>
            </div>
            <Link className="btn sm" href={`/chapter/${r.id}`}>Open →</Link>
          </div>
        ))}
      </div>
    </Card>
  );
}
