"use client";

import React, { useEffect, useState } from "react";
import { ScheduleConfig, ScheduleInfo, studioApi } from "@/lib/api";
import { UndoButton } from "./UndoButton";

const PRESET_PACING = [
  { count: 1, label: "1 / day", desc: "Single Daily Highlight Drop" },
  { count: 2, label: "2 / day", desc: "Morning & Evening Drops" },
  { count: 4, label: "4 / day", desc: "Every 4 Hours" },
  { count: 8, label: "8 / day", desc: "Every 2 Hours (Standard Recaps)", isDefault: true },
  { count: 12, label: "12 / day", desc: "High Velocity (Every ~70 mins)" },
  { count: 24, label: "24 / day", desc: "Blitz (Every hour)" },
];

function fmtHour(h: number): string {
  const ampm = h >= 12 ? "PM" : "AM";
  const num = h % 12 === 0 ? 12 : h % 12;
  return `${num}:00 ${ampm}`;
}

function calculatePacingInterval(postsPerDay: number, startHour: number, endHour: number): string {
  if (postsPerDay <= 1) return `Single post at ${fmtHour(startHour)}`;
  const windowMins = (endHour - startHour) * 60;
  if (windowMins <= 0) return `Distributed across 24h`;
  const stepMins = Math.round(windowMins / (postsPerDay - 1));
  const h = Math.floor(stepMins / 60);
  const m = stepMins % 60;
  if (h > 0 && m > 0) return `Every ${h}h ${m}m`;
  if (h > 0) return `Every ${h} hour${h === 1 ? "" : "s"}`;
  return `Every ${m} mins`;
}

export function PacingThrottle({
  sched,
  onSaved,
}: {
  sched: ScheduleInfo;
  onSaved: (s: ScheduleInfo) => void;
}) {
  const [form, setForm] = useState<ScheduleConfig>({
    timezone: sched.timezone,
    start_hour: sched.start_hour,
    end_hour: sched.end_hour,
    interval_hours: sched.interval_hours,
    posts_per_day: sched.posts_per_day ?? sched.slots_per_day,
    pipeline_overrides: { ...(sched.pipeline_overrides || {}) },
    account_overrides: { ...(sched.account_overrides || {}) },
  });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [undoV, setUndoV] = useState(0);

  const currentPpd = form.posts_per_day ?? sched.slots_per_day ?? 8;
  const currentIntervalText = calculatePacingInterval(currentPpd, form.start_hour, form.end_hour);
  const daysRunway = currentPpd > 0 ? (sched.total_ready / currentPpd).toFixed(1) : "0";

  async function save() {
    setSaving(true);
    setMsg("");
    try {
      const res = await studioApi.saveSchedule(form);
      const updated = await studioApi.schedule();
      onSaved(updated);
      setMsg("✓ Schedule and pacing updated");
      setTimeout(() => setMsg(""), 3500);
    } catch (e: any) {
      setMsg(`Error: ${e.message || String(e)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 10, padding: "14px 16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10, marginBottom: 12 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 16 }}>⚡</span>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Mathematical Pacing Throttle & Velocity Engine</h3>
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
            Eliminates minute drift across the day · Surgical posting cadences across channels
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <UndoButton
            scope="settings"
            version={undoV}
            onUndone={async () => {
              setUndoV((v) => v + 1);
              const reloaded = await studioApi.schedule();
              setForm(reloaded);
              onSaved(reloaded);
            }}
          />
          <button
            type="button"
            disabled={saving}
            onClick={save}
            style={{ fontWeight: 700, fontSize: 12, padding: "5px 14px", background: "var(--accent)", color: "#fff" }}
          >
            {saving ? "Saving…" : "Save pacing"}
          </button>
        </div>
      </div>

      {/* Runway Meter */}
      <div
        style={{
          background: "linear-gradient(135deg, rgba(34, 197, 94, 0.1) 0%, rgba(30, 41, 59, 0.5) 100%)",
          border: "1px solid rgba(34, 197, 94, 0.3)",
          borderRadius: 8,
          padding: "10px 14px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 10,
          marginBottom: 14,
        }}
      >
        <div>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>CONTENT RUNWAY</div>
          <div style={{ fontSize: 16, fontWeight: 800, color: "#4ade80" }}>
            {daysRunway} days left ({sched.total_ready} chapters ready at {currentPpd}/day)
          </div>
        </div>
        <div style={{ fontSize: 11, color: "#cbd5e1" }}>
          Active posting window: <b>{fmtHour(form.start_hour)}</b> to <b>{fmtHour(form.end_hour)}</b> ({currentIntervalText})
        </div>
      </div>

      {/* Preset Pacing Chips */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "var(--muted)", marginBottom: 6 }}>PACING PRESETS</div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {PRESET_PACING.map((p) => {
            const active = currentPpd === p.count;
            return (
              <button
                key={p.count}
                type="button"
                onClick={() => setForm((prev) => ({ ...prev, posts_per_day: p.count }))}
                style={{
                  fontSize: 11,
                  padding: "5px 12px",
                  borderRadius: 6,
                  border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                  background: active ? "var(--accent)" : "var(--chip)",
                  color: active ? "#fff" : "var(--text)",
                  fontWeight: active ? 700 : 500,
                  cursor: "pointer",
                }}
                title={p.desc}
              >
                {p.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Sliders for window */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
        <div>
          <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 4 }}>
            Daily Posts (1 to 48): <b>{currentPpd} posts/day</b>
          </label>
          <input
            type="range"
            min={1}
            max={48}
            value={currentPpd}
            onChange={(e) => setForm((prev) => ({ ...prev, posts_per_day: Number(e.target.value) }))}
            style={{ width: "100%" }}
          />
        </div>

        <div>
          <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 4 }}>
            First Daily Slot: <b>{fmtHour(form.start_hour)}</b>
          </label>
          <input
            type="range"
            min={0}
            max={23}
            value={form.start_hour}
            onChange={(e) => setForm((prev) => ({ ...prev, start_hour: Number(e.target.value) }))}
            style={{ width: "100%" }}
          />
        </div>

        <div>
          <label style={{ fontSize: 11, color: "var(--muted)", display: "block", marginBottom: 4 }}>
            Last Daily Slot: <b>{fmtHour(form.end_hour)}</b>
          </label>
          <input
            type="range"
            min={0}
            max={23}
            value={form.end_hour}
            onChange={(e) => setForm((prev) => ({ ...prev, end_hour: Number(e.target.value) }))}
            style={{ width: "100%" }}
          />
        </div>
      </div>

      {msg && (
        <div style={{ marginTop: 10, fontSize: 11, color: msg.startsWith("✓") ? "#4ade80" : "var(--red)" }}>
          {msg}
        </div>
      )}
    </div>
  );
}
