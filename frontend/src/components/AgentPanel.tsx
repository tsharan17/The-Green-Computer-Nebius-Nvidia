import { useEffect, useRef, useState } from "react";
import AiBadge from "./AiBadge";
import ChatText from "./ChatText";
import { agentChat, api } from "../api";
import type { AgentEvent, AgentStatus } from "../types";

interface Turn {
  role: "user" | "assistant";
  text: string;
  events: AgentEvent[];
  guardrailOk?: boolean;
  error?: string;
  pending?: boolean;
}

const EXAMPLES = [
  "Run YOLOv8s at 30 FPS on a Jetson Orin NX, under 55 W, 40 °C ambient. What should I change?",
  "Why does the baseline throttle, and what is the cheapest fix?",
  "How much of this is measured versus predicted?",
];

const TOOL_LABEL: Record<string, string> = {
  get_project: "Read the project",
  list_devices: "Listed hardware",
  list_models: "Listed models",
  update_project_spec: "Updated the project",
  evaluate: "Evaluated a configuration",
  optimise: "Searched the design space",
  get_candidate: "Inspected a candidate",
  calibration_status: "Checked measurements",
  submit_benchmark: "Launched a GPU benchmark",
  get_benchmark: "Checked a benchmark",
  find_datasheet: "Searched for a datasheet",
  import_hardware: "Drafted a hardware profile",
};

export default function AgentPanel({
  projectId,
  onProjectChanged,
  ask,
}: {
  projectId: number;
  onProjectChanged: () => void;
  /** A question picked on a step's Green Agent box; `n` changes each time one is picked. */
  ask?: { text: string; n: number } | null;
}) {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const asked = useRef(0);

  useEffect(() => {
    api.agentStatus().then(setStatus).catch((e) => setStatus({ configured: false, base_url: "", detail: String(e) }));
  }, []);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  useEffect(() => {
    if (!ask || !status?.configured || busy || asked.current === ask.n) return;
    asked.current = ask.n;
    send(ask.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ask?.n, status?.configured, busy]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    const history = turns
      .filter((t) => t.text && !t.error)
      .slice(-10)
      .map((t) => ({ role: t.role, content: t.text }));
    setTurns((t) => [...t, { role: "user", text: message, events: [] }, { role: "assistant", text: "", events: [], pending: true }]);
    setInput("");
    setBusy(true);
    const patch = (fn: (t: Turn) => Turn) =>
      setTurns((all) => all.map((t, i) => (i === all.length - 1 ? fn(t) : t)));
    try {
      await agentChat({ project_id: projectId, message, history }, (e) => {
        if (e.type === "tool_result" && e.name === "update_project_spec" && e.ok) onProjectChanged();
        patch((t) => {
          const next = { ...t, events: [...t.events, e] };
          if (e.type === "message") {
            next.text = e.text;
            next.guardrailOk = e.guardrail_ok;
          }
          if (e.type === "error") next.error = e.text;
          if (e.type === "done") next.pending = false;
          return next;
        });
      });
    } catch (err) {
      patch((t) => ({ ...t, error: String(err instanceof Error ? err.message : err), pending: false }));
    } finally {
      patch((t) => ({ ...t, pending: false }));
      setBusy(false);
    }
  }

  return (
    <section className="panel agent">
      <div className="row">
        <h2>Green Agent</h2>
        {status?.configured && !status.detail && <span className="chip chip-predicted_feasible">Nemotron connected</span>}
      </div>
      <AiBadge role="plans, calls the engine as tools, explains" />
      <p className="hint">
        Nemotron on Nebius Token Factory plans and explains. The physics engine computes every number, and any figure
        the agent writes that is not in a tool result is rejected. It cannot change your hard constraints.
      </p>
      {status && !status.configured && (
        <p className="note">
          {status.detail ?? "The agent is not configured."} Everything else in the app works without it.
        </p>
      )}
      {status?.configured && status.detail && <p className="note">{status.detail}</p>}
      {status?.configured && status.models && (
        <p className="hint">
          Models: {Object.entries(status.models).map(([k, v]) => `${k}: ${v ?? "not found"}`).join(" · ")}
        </p>
      )}

      <div className="chat">
        {turns.length === 0 && (
          <div className="examples">
            {EXAMPLES.map((ex) => (
              <button key={ex} disabled={!status?.configured || busy} onClick={() => send(ex)}>
                {ex}
              </button>
            ))}
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`turn turn-${t.role}`}>
            {t.role === "assistant" && t.events.length > 0 && (
              <ol className="timeline">
                {t.events.map((e, j) => {
                  if (e.type === "tool_call") return null;
                  if (e.type === "tool_result")
                    return (
                      <li key={j} className={e.ok ? "ok" : "bad"}>
                        <strong>{TOOL_LABEL[e.name] ?? e.name}</strong> <span className="muted">{e.summary}</span>
                      </li>
                    );
                  if (e.type === "guardrail")
                    return (
                      <li key={j} className="bad">
                        <strong>Guardrail</strong>{" "}
                        <span className="muted">
                          {e.action === "rewrite" ? "asked for a rewrite" : "redacted"}: figures not found in tool results ({e.figures.join(", ")})
                        </span>
                      </li>
                    );
                  if (e.type === "status") return <li key={j}><span className="muted">{e.text}</span></li>;
                  return null;
                })}
                {t.pending && <li><span className="muted">working…</span></li>}
              </ol>
            )}
            {t.text && (t.role === "assistant" ? <ChatText text={t.text} /> : <p className="bubble">{t.text}</p>)}
            {t.role === "assistant" && t.text && t.guardrailOk === true && <p className="hint">Every figure above was checked against engine output.</p>}
            {t.role === "assistant" && !t.pending && !t.text && !t.error && (
              <p className="note">The agent finished without writing an answer. Ask again, or ask a narrower question.</p>
            )}
            {t.role === "assistant" && t.guardrailOk === false && <p className="note">Some figures could not be verified and were removed.</p>}
            {t.error && <p className="error">{t.error}</p>}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <textarea
          rows={2}
          value={input}
          disabled={!status?.configured}
          placeholder={status?.configured ? "Describe your goal, e.g. run YOLOv8s at 30 FPS under 55 W at 40 °C" : "Agent disabled until TOKEN_FACTORY_API_KEY is set"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
        />
        <button className="primary" disabled={busy || !status?.configured || !input.trim()} type="submit">
          {busy ? "Working…" : "Send"}
        </button>
      </form>
    </section>
  );
}
