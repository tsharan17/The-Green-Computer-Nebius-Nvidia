import { useId } from "react";
import { GLOSSARY, OUTCOME } from "../lib/glossary";
import { useMode } from "../lib/mode";

/** "Why does this matter?" — a small, keyboard-accessible disclosure next to a metric or term. */
export function Why({ id, label = "Why does this matter?" }: { id: keyof typeof GLOSSARY | string; label?: string }) {
  const c = GLOSSARY[id];
  if (!c) return null;
  return (
    <details className="why">
      <summary>{label}</summary>
      <p>
        <strong>{c.term}.</strong> {c.plain} {c.why}
      </p>
    </details>
  );
}

/** A technical term: plain words in Simple mode, the precise term in Engineering mode, the other
 * one always available on hover/focus. */
export function Term({ id }: { id: string }) {
  const { mode } = useMode();
  const c = GLOSSARY[id];
  const tip = useId();
  if (!c) return <>{id}</>;
  return (
    <span className="term" tabIndex={0} aria-describedby={tip}>
      {mode === "simple" ? c.plain : c.term}
      <span role="tooltip" id={tip} className="term-tip">{mode === "simple" ? c.term : c.plain}</span>
    </span>
  );
}

/** Outcome badge: icon + words + colour, never colour alone. */
export function Outcome({ state, size = "md" }: { state: string | null | undefined; size?: "md" | "lg" }) {
  const o = OUTCOME[state ?? ""];
  if (!o) return <span className="outcome none">Not analysed</span>;
  return (
    <span className={`outcome ${o.cls} ${size}`} title={o.plain}>
      <span aria-hidden className="outcome-icon">{o.icon}</span> {o.label}
    </span>
  );
}

/** The three levels of every major screen. Summary is always shown; "Explain" and "Engineering
 * detail" are disclosures whose default openness follows the display mode. */
export function Level({
  kind,
  title,
  children,
  defaultOpen,
}: {
  kind: "explain" | "engineering";
  title?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const { mode } = useMode();
  const open = defaultOpen ?? mode === "engineering";
  return (
    <details className={`level-block ${kind}`} open={open}>
      <summary>
        <span className="level-tag">{kind === "explain" ? "Explain" : "Engineering detail"}</span>
        {title && <span className="level-title">{title}</span>}
      </summary>
      <div className="level-body">{children}</div>
    </details>
  );
}

/** Human-language error with a recovery action. */
export function ErrorNote({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const status = (error as { status?: number }).status;
  const msg = error instanceof Error ? error.message : String(error);
  const map: Record<number, { title: string; action?: React.ReactNode }> = {
    401: { title: "Your session has ended.", action: <a href="#/signin">Sign in again</a> },
    403: { title: "You do not have permission for this.", action: <a href="#/">Back to your projects</a> },
    404: { title: "This was not found. It may have been deleted, or it belongs to another workspace.", action: <a href="#/">Back to your projects</a> },
    409: { title: "This cannot be done right now." },
    413: { title: "The file is too large." },
    422: { title: "Something in the input needs changing." },
    429: { title: "Too many requests in a short time. Wait a moment, then try again." },
    503: { title: "An optional service is not available on this installation." },
  };
  const m = status ? map[status] : undefined;
  const network = /reach the Green Computer server/.test(msg);
  return (
    <div className="error-note" role="alert">
      <strong>{network ? "The server cannot be reached." : m?.title ?? "Something went wrong."}</strong>
      <p>{msg}</p>
      <div className="error-actions">
        {onRetry && <button onClick={onRetry}>Try again</button>}
        {m?.action}
      </div>
    </div>
  );
}
