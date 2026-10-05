import { Card, PageHead } from "@/components/ui";

// Owner, 2026-10-04: "make another table for the classic system so I can go to
// it when needed". Every area of the previous studio, with where it lives now.
const ROWS: [string, string, string, string][] = [
  ["Board", "/storyboard", "Script, panels, timing for the open chapter", "Board (Check the board)"],
  ["Ingest", "/storyboard?open=ingest", "Paste a chapter link, voice, autopilot card", "Home paste box (Options), Settings"],
  ["Check", "/storyboard?open=validate", "Story check and fixes", "Board → Issues"],
  ["Exports", "/storyboard?open=exports", "Finished videos, expiry, delete", "Board → Watch the video → Version"],
  ["Publishing Studio", "/storyboard?open=publish", "Needs review, ready, queue, published", "Queue"],
  ["Review & Publish (video page)", "/review", "Watch, verdict, SEO, thumbnail, post", "Board → Watch the video"],
  ["Projects", "/storyboard?open=projects", "Every chapter, archive, delete, 🧩 steps", "Library → All chapters, Board step strip"],
  ["Tracker", "/storyboard?open=tracker", "Series board, manage list, sources", "Library (Manage)"],
  ["Logs & Activity", "/storyboard?open=logs", "Live, jobs, spend, what changed", "Activity"],
  ["Settings & Channels", "/storyboard?open=settings", "Voice, channels, schedule, export", "Settings"],
  ["Test lab", "/storyboard?open=test", "Claude pipeline", "Tools → Claude lab"],
  ["Split lab", "/storyboard?open=split", "Splitter preview", "Tools → Split lab"],
];

export default function Classic() {
  return (
    <>
      <PageHead title="Classic studio" sub="The previous studio, kept as a fallback. Each area opens in the old layout; the right column says where it lives now." />
      <Card pad={false}>
        <div className="list">
          {ROWS.map(([name, href, what, now]) => (
            <div className="it" key={name}>
              <div style={{ display: "grid", gap: 2 }}>
                <b>{name}</b>
                <span className="small muted">{what}</span>
              </div>
              <div className="row">
                <span className="small faint">now: {now}</span>
                <a className="btn sm" href={href}>Open classic ↗</a>
              </div>
            </div>
          ))}
        </div>
      </Card>
      <p className="small muted">The classic pages act on the chapter that is open in the studio (the last one you opened on the Board).</p>
    </>
  );
}
