import { Fragment, type ReactNode, useEffect, useState } from "react";
import { auth } from "../api";
import { ErrorNote } from "../components/Help";
import type { LegalDocInfo } from "../workflow/types";

/** Inline markdown: **bold** and [text](href) only (the documents use nothing else). */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<strong key={m.index}>{m[1]}</strong>);
    else out.push(<a key={m.index} href={m[3].startsWith("#") ? m[3] : undefined}>{m[2]}</a>);
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

/** A small renderer for the legal documents: headings, paragraphs, lists and pipe tables. */
export function Markdown({ text }: { text: string }) {
  const blocks = text.replace(/\r/g, "").split(/\n{2,}/);
  return (
    <>
      {blocks.map((b, i) => {
        const lines = b.split("\n");
        if (b.startsWith("# ")) return <h1 key={i}>{b.slice(2)}</h1>;
        if (b.startsWith("## ")) return <h2 key={i}>{b.slice(3)}</h2>;
        if (lines.every((l) => l.startsWith("- ") || l.startsWith("  "))) {
          const items = lines.reduce<string[]>((acc, l) => (l.startsWith("- ") ? [...acc, l.slice(2)] : [...acc.slice(0, -1), `${acc[acc.length - 1]} ${l.trim()}`]), []);
          return <ul key={i}>{items.map((x, j) => <li key={j}>{inline(x)}</li>)}</ul>;
        }
        if (lines[0].startsWith("|")) {
          const cells = (l: string) => l.split("|").slice(1, -1).map((c) => c.trim());
          const [head, , ...rows] = lines;
          return (
            <div className="table-wrap" key={i}>
              <table className="tbl">
                <thead><tr>{cells(head).map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
                <tbody>{rows.map((r, j) => <tr key={j}>{cells(r).map((c, k) => <td key={k}>{inline(c)}</td>)}</tr>)}</tbody>
              </table>
            </div>
          );
        }
        return <p key={i}>{lines.map((l, j) => <Fragment key={j}>{j > 0 && " "}{inline(l)}</Fragment>)}</p>;
      })}
    </>
  );
}

export function LegalLinks() {
  return (
    <nav className="legal-links" aria-label="Legal">
      <a href="#/legal/terms">Terms</a>
      <a href="#/legal/privacy">Privacy</a>
      <a href="#/legal/acceptable-use">Acceptable use</a>
      <a href="#/legal/data">Your data</a>
    </nav>
  );
}

export default function LegalPage({ doc }: { doc: string }) {
  const [docs, setDocs] = useState<LegalDocInfo[]>([]);
  const [body, setBody] = useState<(LegalDocInfo & { markdown: string }) | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    auth.legal().then(setDocs).catch(() => setDocs([]));
  }, []);
  useEffect(() => {
    setBody(null);
    setError(null);
    auth.legalDoc(doc || "terms").then(setBody).catch(setError);
  }, [doc]);
  return (
    <div className="legal">
      <nav className="legal-nav" aria-label="Documents">
        <a href="#/">← Back</a>
        {docs.map((d) => <a key={d.id} href={`#/legal/${d.id}`} aria-current={d.id === (doc || "terms") ? "page" : undefined}>{d.title}</a>)}
      </nav>
      <article className="panel legal-doc">
        <ErrorNote error={error} />
        {body ? (
          <>
            <p className="hint">Version {body.version}</p>
            <Markdown text={body.markdown} />
          </>
        ) : !error && <div className="skeleton" aria-busy="true"><div className="sk-line w60" /><div className="sk-line w90" /></div>}
      </article>
    </div>
  );
}
