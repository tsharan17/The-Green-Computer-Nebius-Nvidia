import { useEffect, useState } from "react";
import { twin } from "../api";
import TraceFigure from "../components/TraceFigure";
import { bytes, ms, num } from "./chart";
import type { StoredTrace } from "./types";

/** The four figures that tell the story of one setup, each with what it shows in plain words. */
export const KEY_FIGS = [
  { name: "thermal-board", title: "Board heat map",
    what: "Where the heat sits on the board once it has warmed up. The chip's temperature is the engine's number; the spread around it is illustrative." },
  { name: "thermal-warmup", title: "Warm-up from a cold start",
    what: "How the chip temperature climbs after it starts working, and whether it reaches the point where it slows itself down." },
  { name: "energy-split", title: "Where the energy goes",
    what: "The energy spent on one input, split by cause: doing the maths, moving data in memory, sitting idle, leakage and fans." },
  { name: "power-timeline", title: "Power over one batch",
    what: "How much power the hardware draws while it works through one batch, with the average marked." },
] as const;

const cache = new Map<string, StoredTrace>();

/** The predicted trace of the current setup, computed once per inputs version and analysis run. */
export function useBaselineTrace(projectId: number, version: number, runKey: number | null) {
  const key = `${projectId}:${version}:${runKey ?? 0}`;
  const [data, setData] = useState<StoredTrace | null>(cache.get(key) ?? null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const hit = cache.get(key);
    if (hit) {
      setData(hit);
      return;
    }
    let live = true;
    setData(null);
    setError(null);
    twin
      .create(projectId, "baseline")
      .then((t) => {
        cache.set(key, t);
        if (live) setData(t);
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [projectId, key]);
  return { data, error };
}

/** Headline numbers of one trace. */
export function TraceKpis({ t }: { t: StoredTrace["trace"] }) {
  const th = t.thermal;
  return (
    <div className="kpis">
      <div><span>Time per input</span><strong className="num">{ms(t.totals.latency_ms)}</strong><small>one batch takes {ms(t.totals.batch_ms)}</small></div>
      <div><span>Average power</span><strong className="num">{num(t.totals.average_power_w)} W</strong><small>{num(t.totals.active_power_w)} W while computing</small></div>
      <div><span>Energy per input</span><strong className="num">{num(t.totals.energy_per_inference_mj)} mJ</strong><small>including idle time between inputs</small></div>
      <div><span>Chip temperature</span><strong className="num">{num(th.steady_c)} °C</strong>
        <small>{th.throttled ? `too hot: slows to ${(th.clock_factor * 100).toFixed(0)}% clock` : `${num(th.throttle_c - th.steady_c)} °C below its limit`}</small></div>
      <div><span>Working memory</span><strong className="num">{bytes(t.totals.peak_working_memory_bytes)}</strong><small>of {bytes(t.totals.memory_capacity_bytes)}</small></div>
    </div>
  );
}

/** The key figures of one setup, or of two side by side (today and with a change). */
export default function KeyFigures({ projectId, a, b, labels }: { projectId: number; a: StoredTrace; b?: StoredTrace; labels?: [string, string] }) {
  return (
    <>
      {KEY_FIGS.map((f) => (
        <section key={f.name} className="panel figure-block" aria-label={f.title}>
          <h3>{f.title}</h3>
          <p className="muted">{f.what}</p>
          {b ? (
            <div className="compare-figs">
              <div><h4>{labels?.[0] ?? "Today"}</h4><TraceFigure projectId={projectId} traceId={a.id} name={f.name} alt={`${f.title}, ${labels?.[0] ?? "today"}`} /></div>
              <div><h4>{labels?.[1] ?? "With the change"}</h4><TraceFigure projectId={projectId} traceId={b.id} name={f.name} alt={`${f.title}, ${labels?.[1] ?? "with the change"}`} /></div>
            </div>
          ) : (
            <TraceFigure projectId={projectId} traceId={a.id} name={f.name} alt={`${f.title} of ${a.trace.model_name} on ${a.trace.device_name}`} />
          )}
        </section>
      ))}
    </>
  );
}
