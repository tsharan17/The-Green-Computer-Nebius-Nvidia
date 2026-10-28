import { useState } from "react";
import type { Advice, Repair } from "../../types";
import { CONFIDENCE_WORDS, EFFORT_WORDS, GLOSSARY, LEVELS } from "../../lib/plain";

function RecCard({ a }: { a: Advice }) {
  const [open, setOpen] = useState(false);
  const c = CONFIDENCE_WORDS[a.confidence];
  return (
    <article className="rec">
      <header>
        <h4>{a.title}</h4>
        <span className="gain">{a.gain}</span>
      </header>
      <p className="rec-do">{a.action}</p>
      <p className="rec-why">{a.why}</p>
      <div className="rec-badges">
        <span className={`conf conf-${a.confidence}`} title={`${c.hint}. ${a.confidence_reason}.`}>
          {c.label}
        </span>
        <span className="effort">{EFFORT_WORDS[a.effort]}</span>
        {a.in_recommended && <span className="inplan">Part of the recommended setup</span>}
        {a.fixes.map((f) => (
          <span key={f} className="fixes">
            fixes: {f}
          </span>
        ))}
      </div>
      {a.tradeoffs.length > 0 && (
        <ul className="tradeoffs">
          {a.tradeoffs.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      )}
      {a.validate_steps.length > 0 && (
        <>
          <button className="link" onClick={() => setOpen(!open)}>
            {open ? "Hide" : "How to check it"}
          </button>
          {open && (
            <ol className="validate">
              {a.validate_steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
              <li className="muted">{a.confidence_reason}.</li>
            </ol>
          )}
        </>
      )}
    </article>
  );
}

export function CheapestFix({ repair }: { repair: Repair }) {
  if (!repair.needed) {
    return (
      <section className="panel fix fix-ok">
        <h2>Nothing is broken</h2>
        <p>{repair.message}</p>
      </section>
    );
  }
  return (
    <section className="panel fix">
      <h2>Do this first: the cheapest fix</h2>
      <p className="hint">{repair.message}</p>
      {repair.plans.length > 0 ? (
        <ol className="fix-plans">
          {repair.plans.map((p, i) => (
            <li key={i}>
              <div className="fix-steps">
                {p.changes.map((c) => (
                  <span key={c} className="step">
                    {c.replace(/^(Software|Hardware \((configure|select)\)|AI model): /, "")}
                  </span>
                ))}
              </div>
              <div className="fix-meta">
                <span className={`chip chip-${p.feasibility}`}>
                  {p.feasibility === "uncertain" ? "Likely, test it" : "Meets every requirement"}
                </span>
                <span className="muted">
                  effort {p.effort_score.toFixed(0)}
                  {p.fixes.length ? ` · fixes: ${p.fixes.join(", ")}` : ""}
                </span>
              </div>
              {p.note && <p className="hint">{p.note}</p>}
            </li>
          ))}
        </ol>
      ) : (
        <p className="error">
          {repair.binding_constraints.length
            ? `These requirements cannot all be met together: ${repair.binding_constraints.join(", ")}.`
            : "No combination meets every requirement."}
        </p>
      )}
    </section>
  );
}

export default function Recommendations({ advice }: { advice: Advice[] }) {
  return (
    <section className="levels">
      <div className="levels-head">
        <h2>What to change, at every level</h2>
        <p className="hint">
          From the AI itself down to the chip. Each idea shows what it gains, why it works, what it costs you and how
          sure we are.
        </p>
      </div>
      <div className="level-grid">
        {LEVELS.map((lv) => {
          const items = advice.filter((a) => lv.tiers.includes(a.tier));
          return (
            <div key={lv.id} className="level" style={{ borderTopColor: lv.accent }}>
              <h3 style={{ color: lv.accent }}>{lv.title}</h3>
              <p className="hint">{lv.subtitle}</p>
              {items.length ? (
                items.map((a) => <RecCard key={a.id} a={a} />)
              ) : (
                <p className="muted empty-level">Nothing worth changing at this level for this workload.</p>
              )}
            </div>
          );
        })}
      </div>
      <p className="hint glossary">
        <strong>Words:</strong> quantise: {GLOSSARY.quantise} · pruning: {GLOSSARY.pruning} · throttle: {GLOSSARY.throttle}
      </p>
    </section>
  );
}
