"use client";
import { api, useApi } from "@/lib/api";
import { ago } from "@/lib/fmt";
import { Busy, Card, ConfirmButton, Empty, PageHead, Pill, useAct } from "@/components/ui";

/** Deleted (owner, 2026-10-14): anything you delete waits here, restorable,
 *  for 14 days (sooner if the disk runs low), then it's gone. */
export default function Deleted() {
  const { data, reload } = useApi<any>("/api/trash", 30000);
  const act = useAct();
  const items: any[] = data?.items || [];
  return (
    <>
      <PageHead title="Deleted" sub={data?.note || "Deleted chapters and merged videos, restorable for 14 days."}
        right={data ? <span className="small muted">{data.free_gb} GB free on the disk</span> : null} />
      <Card pad={false}>
        <div className="list">
          {!data ? <Empty>Loading…</Empty> : items.length === 0 ? <Empty>Nothing deleted in the last {data.days} days.</Empty> : items.map((it) => (
            <div className="it" key={it.name}>
              <div className="row">
                <b>{it.title}</b>
                {it.kind === "range" && <Pill tone="info">merged video</Pill>}
                {it.posted && <Pill tone="ok">was posted</Pill>}
                <span className="small muted">deleted {ago(it.deleted_at)} · {it.videos} video{it.videos === 1 ? "" : "s"} · {it.mb} MB</span>
                <Pill tone={it.days_left <= 2 ? "warn" : "muted"}>{it.days_left} day{it.days_left === 1 ? "" : "s"} left</Pill>
              </div>
              {it.kind === "range" && <div className="small muted">Restoring puts its chapters back inside it (they were released when it was deleted).</div>}
              <div className="row">
                <Busy className="sm primary" disabled={!it.can_restore}
                  title={it.can_restore ? "Bring it back exactly as it was" : "A chapter with this name exists again — delete that one first"}
                  onClick={() => act(async () => { await api("/api/trash/restore", { name: it.name }); reload(); }, `${it.title} restored`)}>↩ Restore</Busy>
                <ConfirmButton className="sm danger" confirm={`Delete ${it.title} forever? This can't be undone.`}
                  onConfirm={() => act(async () => { await api("/api/trash/purge", { name: it.name }); reload(); }, "Deleted forever")}>Delete forever</ConfirmButton>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}
