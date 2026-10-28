import { useEffect, useState } from "react";
import { workflow } from "../api";

/** Each figure with what it answers and how to read it, in plain words. */
const FIGS = [
  {
    name: "cooling-headroom", tab: "Room temperature limit", title: "How warm a room can each cooling option handle?",
    what: "One bar per cooling option: the warmest room it can run in at today's input rate before the chip gets too hot and slows itself down.",
    read: "Bars that reach past the dashed line (your room) are fine here. Green has at least 5 °C to spare, amber is close, red slows down in your room.",
    alt: "Bar chart of the warmest ambient temperature each cooling option can handle without throttling, with the project's ambient marked",
  },
  {
    name: "cooling-heatmap", tab: "Room temperature map", title: "Chip temperature for every cooling option and room temperature",
    what: "Each cell is the chip temperature the engine predicts for one cooling option (row) in one room temperature (column), at today's input rate.",
    read: "The boxed column is your room. A star means the chip hit its limit and slowed down to stay there, so that cell is too hot.",
    alt: "Heatmap of steady chip temperature for each cooling option across ambient temperatures, with throttling cells starred",
  },
  {
    name: "mode-cooling-heatmap", tab: "Power mode map", title: "Chip temperature for every power mode and cooling option",
    what: "Each cell is the chip temperature for one cooling option (row) and one power mode (column), in your room at today's input rate.",
    read: "A lower power mode makes less heat but runs slower. Look for a row and column that stay unstarred.",
    alt: "Heatmap of steady chip temperature for each power mode and cooling option at the project's ambient temperature",
  },
] as const;

function Fig({ projectId, name, alt, version }: { projectId: number; name: string; alt: string; version: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setUrl(null);
    setError(null);
    workflow.figure(projectId, name).then((u) => live && setUrl(u)).catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [projectId, name, version]);
  if (error) return <p className="note" role="status">This figure is not available for this setup: {error}.</p>;
  if (!url) return <div className="mpl-fig mpl-loading" aria-busy="true">Drawing the figure…</div>;
  return (
    <figure className="mpl">
      <img className="mpl-fig" src={url} alt={alt} />
      <a className="link mpl-save" href={url} download={`${name}.png`}>Save as PNG</a>
    </figure>
  );
}

/** Cooling figures for today's setup: every bar and cell is one engine evaluation (steady state,
 * lumped thermal model), never interpolated beyond what the caption says. */
export default function CoolingFigures({ projectId, version }: { projectId: number; version: number }) {
  const [open, setOpen] = useState<string>(FIGS[0].name);
  const f = FIGS.find((x) => x.name === open)!;
  return (
    <section className="panel" aria-labelledby="cf-h">
      <h2 id="cf-h">Cooling: how much heat can it take?</h2>
      <p className="muted">
        The chip turns power into heat; the cooler moves that heat into the room. The warmer the room, or the weaker the cooler, the hotter the
        chip runs. When it reaches its limit it slows itself down (throttles). These charts show where that happens for today's model and settings.
      </p>
      <div className="seg" role="tablist" aria-label="Cooling figure">
        {FIGS.map((x) => (
          <button key={x.name} role="tab" aria-selected={open === x.name} className={open === x.name ? "on" : ""} onClick={() => setOpen(x.name)}>
            {x.tab}
          </button>
        ))}
      </div>
      <h3>{f.title}</h3>
      <dl className="fig-explain">
        <div><dt>What it shows</dt><dd>{f.what}</dd></div>
        <div><dt>How to read it</dt><dd>{f.read}</dd></div>
      </dl>
      <Fig projectId={projectId} name={f.name} alt={f.alt} version={version} />
      <p className="hint">Predicted by the analytical engine at steady state. It is not a board-level or CFD thermal simulation.</p>
    </section>
  );
}
