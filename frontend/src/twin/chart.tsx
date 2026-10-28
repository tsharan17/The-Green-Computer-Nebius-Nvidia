import { useRef, useState } from "react";

/** Shared chart pieces. Series colours come from the validated categorical slots (--s1..--s5) in
 * fixed order; text always wears ink tokens, never a series colour. */

export const ENERGY_KEYS = ["compute", "memory", "idle", "leakage", "cooling"] as const;
export const ENERGY_LABEL: Record<(typeof ENERGY_KEYS)[number], string> = {
  compute: "Compute (dynamic)",
  memory: "Memory traffic",
  idle: "Idle / uncore",
  leakage: "Leakage",
  cooling: "Cooling (fan)",
};
export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)"];

export function bytes(n: number): string {
  if (!Number.isFinite(n)) return "–";
  const a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(a >= 1e8 ? 0 : 1)} MB`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)} KB`;
  return `${Math.round(n)} B`;
}

export function num(n: number, digits = 3): string {
  if (!Number.isFinite(n)) return "–";
  if (n === 0) return "0";
  const a = Math.abs(n);
  if (a >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return Number(n.toPrecision(digits)).toString();
}

export function ms(n: number): string {
  if (n >= 1000) return `${(n / 1000).toPrecision(3)} s`;
  if (n >= 1) return `${num(n)} ms`;
  return `${num(n * 1000)} µs`;
}

/** Hover tooltip anchored inside a relatively positioned chart box. */
export function useTip() {
  const box = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; body: React.ReactNode } | null>(null);
  const show = (e: React.MouseEvent | React.FocusEvent, body: React.ReactNode) => {
    const r = box.current?.getBoundingClientRect();
    if (!r) return;
    const target = (e.target as Element).getBoundingClientRect();
    const cx = "clientX" in e && e.clientX ? e.clientX : target.left + target.width / 2;
    const cy = "clientY" in e && e.clientY ? e.clientY : target.top;
    setTip({ x: Math.min(cx - r.left + 12, r.width - 240), y: cy - r.top + 12, body });
  };
  const hide = () => setTip(null);
  const node = tip ? (
    <div className="viz-tip" style={{ left: Math.max(0, tip.x), top: tip.y }} role="status">
      {tip.body}
    </div>
  ) : null;
  return { box, show, hide, node };
}

export function Legend({ items }: { items: { label: string; color: string; value?: string }[] }) {
  return (
    <ul className="viz-legend">
      {items.map((i) => (
        <li key={i.label}>
          <span className="viz-sw" style={{ background: i.color }} aria-hidden />
          {i.label}
          {i.value && <strong>{i.value}</strong>}
        </li>
      ))}
    </ul>
  );
}

/** Chart frame: title, provenance note, the chart, and a "Show table" toggle (the accessible and
 * colour-independent view of the same data). */
export function Figure({
  title,
  note,
  actions,
  table,
  children,
}: {
  title: string;
  note?: React.ReactNode;
  actions?: React.ReactNode;
  table?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);
  return (
    <figure className="viz panel">
      <figcaption className="viz-head">
        <div>
          <h2>{title}</h2>
          {note && <p className="hint">{note}</p>}
        </div>
        <div className="viz-actions">
          {table && (
            <button className="link" aria-pressed={showTable} onClick={() => setShowTable((s) => !s)}>
              {showTable ? "Show chart" : "Show table"}
            </button>
          )}
          {actions}
        </div>
      </figcaption>
      {showTable && table ? <div className="table-wrap viz-table">{table}</div> : children}
    </figure>
  );
}
