import { useEffect, useId, useRef, useState } from "react";
import { guide, type SearchHit } from "../api";

const TYPE_LABEL: Record<string, string> = {
  project: "Project", plan: "Plan", run: "Analysis", recommendation: "Recommendation", hardware: "Hardware",
  model: "Model", component: "Component", tensor: "Tensor",
};

/** Search projects, plans, analyses, recommendations, hardware, models, components and tensors.
 * Ctrl/Cmd+K focuses it; arrow keys move; Enter opens; Escape closes. */
export default function GlobalSearch() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useId();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (q.trim().length < 2) {
      setHits([]);
      return;
    }
    const t = window.setTimeout(() => {
      guide.search(q).then((h) => { setHits(h); setActive(0); setOpen(true); }).catch(() => setHits([]));
    }, 220);
    return () => window.clearTimeout(t);
  }, [q]);

  function go(h: SearchHit) {
    window.location.hash = h.href.replace(/^#/, "");
    setOpen(false);
    setQ("");
  }

  return (
    <div className="gsearch">
      <label className="sr-only" htmlFor={`${list}-in`}>Search</label>
      <input
        id={`${list}-in`}
        ref={input}
        type="search"
        placeholder="Search projects, plans, hardware… (Ctrl+K)"
        value={q}
        role="combobox"
        aria-expanded={open && hits.length > 0}
        aria-controls={list}
        aria-activedescendant={open && hits[active] ? `${list}-${active}` : undefined}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => hits.length && setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter" && hits[active]) go(hits[active]);
          else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && q.trim().length >= 2 && (
        <ul id={list} role="listbox" className="gsearch-list">
          {hits.length === 0 && <li className="muted gs-empty">Nothing matches “{q}”.</li>}
          {hits.map((h, i) => (
            <li key={`${h.type}-${h.title}-${h.href}`} id={`${list}-${i}`} role="option" aria-selected={i === active}
              className={i === active ? "on" : ""} onMouseDown={(e) => { e.preventDefault(); go(h); }} onMouseEnter={() => setActive(i)}>
              <span className="gs-type">{TYPE_LABEL[h.type] ?? h.type}</span>
              <span className="gs-title">{h.title}</span>
              <span className="gs-sub muted">{h.subtitle}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
