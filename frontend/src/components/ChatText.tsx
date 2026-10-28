import type { ReactNode } from "react";

/** The agent's replies in Markdown, rendered as React nodes (never as HTML): headings, paragraphs,
 * bullet and numbered lists, pipe tables, rules, **bold**, *italic* and `code`. */

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*([^*]+)\*\*|\*([^*\s][^*]*)\*|`([^`]+)`/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) out.push(<strong key={m.index}>{m[1]}</strong>);
    else if (m[2] !== undefined) out.push(<em key={m.index}>{m[2]}</em>);
    else out.push(<code key={m.index}>{m[3]}</code>);
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

const cells = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
const BULLET = /^\s*[-*•]\s+/;
const NUMBER = /^\s*\d+[.)]\s+/;

export default function ChatText({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    const key = blocks.length;
    if (!l.trim()) {
      i++;
    } else if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) {
      blocks.push(<hr key={key} />);
      i++;
    } else if (/^#{1,6}\s/.test(l)) {
      blocks.push(<p key={key} className="chat-h">{inline(l.replace(/^#+\s*/, ""))}</p>);
      i++;
    } else if (l.trim().startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++]);
      const body = rows.slice(1).filter((r) => !/^[\s|:-]+$/.test(r));
      blocks.push(
        <div className="table-wrap" key={key}>
          <table className="tbl">
            <thead><tr>{cells(rows[0]).map((c, k) => <th key={k} scope="col">{inline(c)}</th>)}</tr></thead>
            <tbody>{body.map((r, j) => <tr key={j}>{cells(r).map((c, k) => <td key={k}>{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
    } else if (BULLET.test(l) || NUMBER.test(l)) {
      const re = BULLET.test(l) ? BULLET : NUMBER;
      const items: string[] = [];
      while (i < lines.length && (re.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        if (re.test(lines[i])) items.push(lines[i].replace(re, ""));
        else items[items.length - 1] += ` ${lines[i].trim()}`;
        i++;
      }
      const li = items.map((x, j) => <li key={j}>{inline(x)}</li>);
      blocks.push(re === BULLET ? <ul key={key}>{li}</ul> : <ol key={key}>{li}</ol>);
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*\||\s*[-*•]\s|\s*\d+[.)]\s|\s*-{3,}\s*$)/.test(lines[i]))
        para.push(lines[i++]);
      if (!para.length) para.push(lines[i++]);
      blocks.push(<p key={key}>{inline(para.join(" "))}</p>);
    }
  }
  return <div className="bubble chat-md">{blocks}</div>;
}
