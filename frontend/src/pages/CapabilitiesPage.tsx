import { useEffect, useState } from "react";
import { intake } from "../api";
import type { Capability } from "../types";

const STATUS: Record<Capability["status"], { label: string; cls: string }> = {
  available: { label: "Available here", cls: "chip-predicted_feasible" },
  needs_credential: { label: "Needs a credential", cls: "chip-uncertain" },
  disabled: { label: "Installed, turned off", cls: "chip-uncertain" },
  unavailable: { label: "Unavailable here", cls: "chip-infeasible" },
  planned: { label: "Planned", cls: "" },
};

export default function CapabilitiesPage() {
  const [caps, setCaps] = useState<Capability[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    intake.capabilities().then(setCaps).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const groups = caps ? Array.from(new Set(caps.map((c) => c.group))) : [];
  const counts = caps
    ? (Object.keys(STATUS) as Capability["status"][]).map((s) => [s, caps.filter((c) => c.status === s).length] as const).filter(([, n]) => n)
    : [];

  return (
    <div className="capabilities">
      <header className="ws-head">
        <h1>What this installation can do</h1>
        <p className="muted">
          Checked live on the server. Everything marked available works offline with no account at any vendor. GPU execution and the
          AI assistant are optional accelerators; nothing in the analysis depends on them.
        </p>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {caps === null && !error && <div className="panel skeleton" aria-busy="true"><div className="sk-line w75" /><div className="sk-line w60" /></div>}
      {caps && (
        <p className="cap-summary">
          {counts.map(([s, n]) => (
            <span key={s} className={`chip ${STATUS[s].cls}`}>{n} {STATUS[s].label.toLowerCase()}</span>
          ))}
        </p>
      )}
      {groups.map((g) => (
        <section key={g} className="panel" aria-labelledby={`cap-${g}`}>
          <h2 id={`cap-${g}`}>{g}</h2>
          <ul className="cap-list">
            {caps!.filter((c) => c.group === g).map((c) => (
              <li key={c.id}>
                <div className="row">
                  <strong>{c.name}</strong>
                  <span className={`chip ${STATUS[c.status].cls}`}>{STATUS[c.status].label}</span>
                </div>
                <p>{c.detail}</p>
                {c.measures && c.measures.length > 0 && <p className="muted">Measures: {c.measures.join(", ")}.</p>}
                {c.enable && c.status !== "available" && <p className="hint">To enable: {c.enable}</p>}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
