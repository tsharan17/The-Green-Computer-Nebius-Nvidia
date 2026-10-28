/** Marks a feature that calls NVIDIA Nemotron through Nebius Token Factory, and what the model does there.
 * Everything without this badge is computed by the deterministic engine. `compact` fits a narrow column and
 * keeps the detail in the tooltip. */
export default function AiBadge({ role, extra, compact }: { role: string; extra?: string; compact?: boolean }) {
  return (
    <span
      className={`ai-badge${compact ? " compact" : ""}`}
      title={`Runs NVIDIA Nemotron on Nebius Token Factory${extra ? ` with ${extra}` : ""}: ${role}. The engine still computes every number.`}
    >
      <span className="ai-badge-tag" aria-hidden>AI</span>
      {compact ? "NVIDIA Nemotron" : "NVIDIA Nemotron on Nebius Token Factory"}{extra ? ` + ${extra}` : ""}{!compact && ":"}
      {!compact && <span className="ai-badge-role">{role}</span>}
    </span>
  );
}
