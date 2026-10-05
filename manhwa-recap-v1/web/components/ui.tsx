"use client";
import { ReactNode, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

export type Status = { key: string; label: string; tone: string; hint?: string; reason?: string | null; stage?: string };

const ICON: Record<string, string> = {
  found: "○", making: "⟳", waiting: "⏸", failed: "✕", to_review: "◉", rendering: "⟳",
  video_ready: "▶", scheduled: "⏱", posting: "↑", posted: "✓", archived: "▣",
};

export function StatusPill({ s, title }: { s?: Status | null; title?: string }) {
  if (!s) return null;
  return (
    <span className={`pill t-${s.tone}`} title={title || s.hint}>
      <span aria-hidden>{ICON[s.key] || "•"}</span>
      {s.label}
    </span>
  );
}

export function Pill({ tone = "muted", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return <span className={`pill t-${tone}`} title={title}>{children}</span>;
}

export function Card({ title, right, children, pad = true }: { title?: ReactNode; right?: ReactNode; children: ReactNode; pad?: boolean }) {
  return (
    <section className="card">
      {(title || right) && (
        <div className="hd">
          {typeof title === "string" ? <h2>{title}</h2> : title}
          {right}
        </div>
      )}
      {pad ? <div className="bd">{children}</div> : children}
    </section>
  );
}

export function PageHead({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="pagehead">
      <div>
        <h1>{title}</h1>
        {sub && <div className="sub">{sub}</div>}
      </div>
      {right && <div className="row">{right}</div>}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: [T, ReactNode][]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([k, l]) => (
        <button key={k} role="tab" aria-selected={value === k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>
          {l}
        </button>
      ))}
    </div>
  );
}

export function Chips<T extends string>({ items, value, onChange }: { items: [T, ReactNode][]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="chips">
      {items.map(([k, l]) => (
        <button key={k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>
          {l}
        </button>
      ))}
    </div>
  );
}

/** Two taps for anything that spends money, posts, or deletes. No browser pop-ups. */
export function ConfirmButton({ onConfirm, children, confirm, className = "", disabled, title }:
  { onConfirm: () => Promise<any> | any; children: ReactNode; confirm: string; className?: string; disabled?: boolean; title?: string }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const t = useRef<any>(null);
  useEffect(() => () => clearTimeout(t.current), []);
  return (
    <button
      className={`${className} ${armed ? "armed" : ""}`}
      disabled={disabled || busy}
      title={title}
      onClick={async () => {
        if (!armed) {
          setArmed(true);
          t.current = setTimeout(() => setArmed(false), 4500);
          return;
        }
        clearTimeout(t.current);
        setArmed(false);
        setBusy(true);
        try { await onConfirm(); } finally { setBusy(false); }
      }}
    >
      {busy ? "…" : armed ? confirm : children}
    </button>
  );
}

export function Busy({ onClick, children, className = "", disabled, title }:
  { onClick: () => Promise<any> | any; children: ReactNode; className?: string; disabled?: boolean; title?: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <button className={className} disabled={disabled || busy} title={title}
      onClick={async () => { setBusy(true); try { await onClick(); } finally { setBusy(false); } }}>
      {busy ? "…" : children}
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Meter({ label, value, max, fmt }: { label: string; value: number; max: number; fmt: (n: number) => string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="meter">
      <div className="row"><span>{label}</span><span className="num">{fmt(value)} / {fmt(max)}</span></div>
      <div className="bar"><i className={pct > 90 ? "bad" : pct > 70 ? "warn" : ""} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

// ---- toasts
type ToastT = { text: string; bad?: boolean } | null;
const ToastCtx = createContext<(t: string, bad?: boolean) => void>(() => {});
export function ToastHost({ children }: { children: ReactNode }) {
  const [t, setT] = useState<ToastT>(null);
  const timer = useRef<any>(null);
  const show = useCallback((text: string, bad?: boolean) => {
    setT({ text, bad });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setT(null), bad ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {t && <div className={`toast ${t.bad ? "bad" : ""}`} role="status">{t.text}</div>}
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/** Run an action, toast the result or the error. */
export function useAct() {
  const toast = useToast();
  return useCallback(async (fn: () => Promise<any>, ok?: string) => {
    try {
      const r = await fn();
      if (ok) toast(ok);
      return r;
    } catch (e: any) {
      toast(e.message || String(e), true);
      return undefined;
    }
  }, [toast]);
}
