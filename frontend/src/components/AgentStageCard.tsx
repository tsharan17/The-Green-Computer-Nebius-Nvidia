import { useEffect, useState } from "react";
import AiBadge from "./AiBadge";
import { api } from "../api";
import type { Step } from "../flow";
import type { AgentStatus, OptimisationResult, ProjectSpec } from "../types";

/** What the Green Agent can do on the current step, beside the step header. The text and the
 * questions are built from the project (model, device, limits, today's verdict), so they change as
 * the user moves through the stages and edits the inputs. A question opens the assistant and asks it. */

interface Ctx {
  model: string;
  device: string;
  spec: ProjectSpec;
  res: OptimisationResult | null;
}

interface StageHelp {
  does: string;
  tools: string[];
  questions: string[];
}

let statusOnce: Promise<AgentStatus> | null = null;
function agentStatus(): Promise<AgentStatus> {
  statusOnce ??= api.agentStatus().catch((e) => {
    statusOnce = null;
    return { configured: false, base_url: "", detail: String(e) };
  });
  return statusOnce;
}

function helpFor(stepId: string, c: Ctx): StageHelp {
  const { model, device, spec, res } = c;
  const fps = spec.workload.fps;
  const watts = spec.constraints.max_power_w;
  const room = spec.workload.ambient_c;
  const ok = res?.verdict?.baseline.ok;
  switch (stepId) {
    case "input":
      return {
        does: `Compares ${device} with other boards in the library under your limits, suggests a model that fits, and drafts a profile from the web for hardware that is not listed.`,
        tools: ["Lists hardware and models", "Evaluates each option", "Reads datasheets (Tavily)"],
        questions: [
          `Which board in the library runs ${model} at ${fps} inputs a second under ${watts} W?`,
          `Would a smaller model than ${model} meet my limits on ${device}?`,
          "Import the Hailo-8L from its datasheet and tell me which values are missing.",
        ],
      };
    case "constraints":
      return {
        does: "Tests your requirements and environment against the engine: which limit is hardest to meet and how much margin each one has. It cannot change your limits; only you can.",
        tools: ["Reads your requirements", "Evaluates what-ifs"],
        questions: [
          `Which of my requirements is hardest for ${device} to meet, and by how much?`,
          `What is the warmest room ${device} can handle at ${fps} inputs a second?`,
        ],
      };
    case "check":
      return {
        does: "Explains each flagged input in plain words and says what to fix first, and which values are assumed rather than measured.",
        tools: ["Reads the project", "Checks measurements"],
        questions: [
          "Explain what was flagged in my inputs and what I should fix first.",
          "Which of my inputs are assumed rather than measured?",
        ],
      };
    case "summary":
      if (!res)
        return {
          does: "Runs the analysis for you and explains the verdict, the limiting factor and what drives it.",
          tools: ["Searches the design space", "Explains the result"],
          questions: [`Analyse ${model} on ${device} and explain the result in plain words.`],
        };
      return {
        does: ok
          ? `Explains why ${model} passes on ${device}, how much headroom is left, and what would break it.`
          : `Explains why ${model} fails on ${device} today${res.limiting_factor ? ` (limited by ${res.limiting_factor.split(":")[0]})` : ""}, and tries changes until one passes.`,
        tools: ["Evaluates configurations", "Searches the design space", "Explains the result"],
        questions: ok
          ? [
              `How much headroom is there before ${device} throttles?`,
              `Would it still pass in a room ${room + 10} °C warmer than now?`,
            ]
          : [
              `Why does ${model} fail on ${device} today, in plain words?`,
              "What is the smallest change that makes it pass, and what does it cost in accuracy?",
            ],
      };
    case "engine":
      return {
        does: "Searches thousands of configurations, compares the top candidates and explains the trade-off. It also tries other boards from the library, which the list on this page does not.",
        tools: ["Searches the design space", "Inspects candidates", "Evaluates other boards"],
        questions: [
          "Compare the top two changes and tell me the trade-off between them.",
          `Is switching to another board from the library better than changing settings on ${device}?`,
        ],
      };
    case "plan":
    case "saved":
      return {
        does: "Explains how software and hardware changes interact when combined, and which plan saves the most energy for the least effort.",
        tools: ["Searches the design space", "Compares software, hardware and combined"],
        questions: [
          "If I combine the best software change with the best hardware change, do they help or hurt each other?",
          "Which change saves the most energy per year for the least effort?",
        ],
      };
    case "run":
    case "measured":
      return {
        does: `Says how much of the analysis is measured and how much is predicted, and whether the calibration for ${device} can be trusted.`,
        tools: ["Checks measurements", "Reads calibration"],
        questions: [
          "How much of this analysis is measured versus predicted?",
          `Is the calibration for ${device} good enough to trust?`,
        ],
      };
    default:
      return {
        does: "Writes a plain summary of the decision with the engine's numbers, and lists the assumptions a reviewer should check.",
        tools: ["Reads the analysis", "Explains the result"],
        questions: [
          `Summarise the decision for ${model} on ${device} for a manager in five sentences.`,
          "List the assumptions a reviewer should check before we build this.",
        ],
      };
  }
}

export default function AgentStageCard({
  step,
  model,
  device,
  spec,
  res,
  canWrite,
  onAsk,
}: Ctx & { step: Step; canWrite: boolean; onAsk: (question: string) => void }) {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  useEffect(() => {
    let live = true;
    agentStatus().then((s) => live && setStatus(s));
    return () => {
      live = false;
    };
  }, []);
  const help = helpFor(step.id, { model, device, spec, res });
  const off = status !== null && !status.configured;
  const note = off
    ? "Not configured on this server (needs TOKEN_FACTORY_API_KEY). Everything else works without it."
    : !canWrite
      ? "Your role is read-only, so the agent is off for you."
      : null;
  return (
    <aside className="agent-stage" aria-label="Green Agent on this step">
      <p className="agent-stage-does" title={`Uses: ${help.tools.join(", ")}. The engine computes every number; figures not in its results are rejected.`}>
        <AiBadge compact role="plans, calls the engine as tools, explains" />
        <strong>Green Agent:</strong> {help.does}
      </p>
      <div className="agent-stage-qs">
        {help.questions.map((q) => (
          <button key={q} type="button" disabled={!!note} onClick={() => onAsk(q)}>
            <span aria-hidden>→</span> {q}
          </button>
        ))}
      </div>
      {note && <p className="agent-stage-foot">{note}</p>}
    </aside>
  );
}
