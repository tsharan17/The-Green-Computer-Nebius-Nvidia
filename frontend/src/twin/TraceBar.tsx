import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { download, plans, twin } from "../api";
import type { StoredTrace } from "./types";

export type Which = "baseline" | "best" | "plan";

/** Changes the user picked in Recommendations; the stage views can then show their trace. */
export interface PlanPick { key: string; label: string; changeIds: string[] }

/** One trace selection shared by every stage of a project: the setup chosen on one page stays
 * chosen on the next, and a trace computed once is reused. Keyed by setup, project version and the
 * analysis run, so a new analysis never shows a stale trace. */
function useTraceStore(projectId: number, version: number, hasBest: boolean, runKey: number | null) {
  // Analysis always opens on the current setup; changes are shown only once the user picks them.
  const [which, setWhich] = useState<Which>("baseline");
  const [plan, setPlan] = useState<PlanPick | null>(null);
  const [data, setData] = useState<StoredTrace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cache = useRef(new Map<string, StoredTrace>());

  const load = useCallback(
    async (force = false) => {
      if (which === "plan" && !plan) return;
      const key = `${which === "plan" ? `plan:${plan?.key}` : which}:${version}:${runKey ?? 0}`;
      const hit = cache.current.get(key);
      if (hit && !force) {
        setData(hit);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const t = which === "plan" && plan ? await plans.trace(projectId, plan.changeIds) : await twin.create(projectId, which === "plan" ? "baseline" : which);
        cache.current.set(key, t);
        setData(t);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setData(null);
      } finally {
        setLoading(false);
      }
    },
    [projectId, version, runKey, which, plan],
  );

  // A plan analysed against an earlier analysis no longer applies.
  useEffect(() => {
    setPlan(null);
  }, [runKey]);
  useEffect(() => {
    if (which === "best" && !hasBest) setWhich("baseline");
    if (which === "plan" && !plan) setWhich("baseline");
  }, [hasBest, which, plan]);
  return { which, setWhich, plan, setPlan, data, loading, error, load, recompute: () => load(true) };
}

type TraceStore = ReturnType<typeof useTraceStore>;
const TraceCtx = createContext<TraceStore | null>(null);

export function TraceProvider({ projectId, version, hasBest, runKey, children }: { projectId: number; version: number; hasBest: boolean; runKey: number | null; children: ReactNode }) {
  const store = useTraceStore(projectId, version, hasBest, runKey);
  return <TraceCtx.Provider value={store}>{children}</TraceCtx.Provider>;
}

/** Fetch (or reuse) the predicted trace for the setup chosen for this project. */
export function useTrace(projectId: number, version: number, hasBest: boolean) {
  const shared = useContext(TraceCtx);
  const local = useTraceStore(projectId, version, hasBest, null);
  const s = shared ?? local;
  const { load } = s;
  useEffect(() => {
    load();
  }, [load]);
  return s;
}

/** Tell the stage views which analysed plan to offer next to the current and recommended setups. */
export function usePlanPicker() {
  return useContext(TraceCtx)?.setPlan ?? null;
}
const PROV: Record<string, string> = {
  analytical: "Predicted by the analytical engine",
  calibrated: "Predicted, calibrated against your measurements",
  estimated: "Measured total, predicted split between operators",
  measured: "Measured on a real run",
  imported: "Imported from a device log",
};

export function TraceBar({
  projectId,
  state,
  csvTable,
}: {
  projectId: number;
  state: ReturnType<typeof useTrace>;
  /** Kept for callers; the analysis views always start from the current setup. */
  hasBest?: boolean;
  csvTable?: "events" | "memory" | "tensors";
}) {
  const { which, setWhich, data, loading, error, recompute } = state;
  const t = data?.trace;
  return (
    <div className="tracebar" role="region" aria-label="Trace selection">
      <div className="seg" role="radiogroup" aria-label="Which setup">
        <button role="radio" aria-checked={which === "baseline"} className={which === "baseline" ? "on" : ""} onClick={() => setWhich("baseline")}>
          Current setup
        </button>
        {state.plan && (
          <button role="radio" aria-checked={which === "plan"} className={which === "plan" ? "on" : ""} title={state.plan.label} onClick={() => setWhich("plan")}>
            With your selected changes
          </button>
        )}
      </div>
      {t && (
        <span className={`badge ${t.kind === "measured" ? "badge-measured" : "badge-analytical"}`} title={t.source}>
          {PROV[t.provenance] ?? t.provenance}
        </span>
      )}
      {loading && <span className="muted">Computing the trace…</span>}
      <span className="grow" />
      {data && (
        <>
          <button className="link" onClick={() => download(`/projects/${projectId}/traces/${data.id}/export?format=json`, "trace.json")}>
            Trace JSON
          </button>
          {csvTable && (
            <button className="link" onClick={() => download(`/projects/${projectId}/traces/${data.id}/export?format=csv&table=${csvTable}`, `trace-${csvTable}.csv`)}>
              {csvTable === "events" ? "Events CSV" : csvTable === "memory" ? "Memory CSV" : "Tensors CSV"}
            </button>
          )}
          <button className="link" onClick={recompute} disabled={loading}>Recompute</button>
        </>
      )}
      {error && <p className="error full" role="alert">{error}</p>}
      {which === "plan" && <p className="note full">These views show today's setup with the changes you picked in the recommendation engine. Chip-design what-ifs are directional and are not drawn here.</p>}
      {t && t.fidelity !== "graph" && <p className="note full">{t.fidelity_note}</p>}
    </div>
  );
}
