"use client";
import Link from "next/link";
import { api, useApi } from "@/lib/api";
import { ago, money } from "@/lib/fmt";
import { Card, Empty, PageHead, StatusPill } from "@/components/ui";
import PasteBox from "@/components/PasteBox";

const ACTION_HREF = (n: any) =>
  n.kind === "demand" ? "/library" : n.kind === "video_ready" ? `/chapter/${n.id}?tab=video` : `/chapter/${n.id}`;

export default function Home() {
  const { data: h, reload } = useApi<any>("/api/home", 15000);
  const ev = useApi<any>("/api/events?after=0&limit=12", 15000);
  return (
    <>
      <PageHead title="Home" sub="Paste a link to start. Everything that needs you is listed below." />
      <PasteBox onDone={reload} />
      <div className="tiles">
        <Tile n={(h?.counts?.video_ready || 0)} label="videos to watch and approve" href="/queue" />
        <Tile n={(h?.counts?.to_review || 0)} label="boards to review" href="/library" />
        <Tile n={h?.queue?.schedule_on ? (h.queue.next_post || "—") : "off"} label={h?.queue?.schedule_on ? `next post · ${h?.queue?.scheduled || 0} scheduled` : "posting schedule"} href="/queue" />
        <Tile n={h ? money(h.spend.today) : "—"} label={h ? `of ${money(h.spend.cap)} today · autopilot ${money(h.spend.autopilot)}` : "spend today"} href="/activity?tab=spend" />
      </div>
      <Card title="Needs you" pad={false}>
        {!h ? <Empty>Loading…</Empty> : h.need.length === 0 ? <Empty>Nothing waiting for you. New chapters arrive here when they are ready.</Empty> : (
          <div className="list">
            {h.need.map((n: any, i: number) => (
              <div className="it" key={i}>
                <div className="row" style={{ minWidth: 0 }}>
                  <StatusPill s={n.status} />
                  <b>{n.title}</b>
                  {n.status?.reason && <span className="small muted">{String(n.status.reason).slice(0, 120)}</span>}
                </div>
                <Link className="btn sm" href={ACTION_HREF(n)}>{n.action} →</Link>
              </div>
            ))}
          </div>
        )}
      </Card>
      <Card title="Latest activity" right={<Link className="small" href="/activity">All activity →</Link>} pad={false}>
        <div className="list">
          {(ev.data?.events || []).slice().reverse().map((e: any) => (
            <div className="it" key={e.id}>
              <span className={e.level === "error" ? "" : e.level === "warn" ? "" : "muted"} style={{ color: e.level === "error" ? "var(--red)" : e.level === "warn" ? "var(--yellow)" : undefined }}>{e.msg}</span>
              <span className="small faint">{ago(e.ts)}</span>
            </div>
          ))}
          {!ev.data?.events?.length && <Empty>No activity yet.</Empty>}
        </div>
      </Card>
    </>
  );
}

function Tile({ n, label, href }: { n: any; label: string; href: string }) {
  return (
    <Link href={href} className="tile" style={{ color: "inherit", textDecoration: "none" }}>
      <b>{n}</b>
      <span>{label}</span>
    </Link>
  );
}

