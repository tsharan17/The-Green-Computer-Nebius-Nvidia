import { useEffect, useState } from "react";
import { twin } from "../api";

/** A matplotlib/seaborn figure drawn by the server from a stored trace. The numbers are the
 * trace's own; the caption and the figure's footer carry the provenance. */
export default function TraceFigure({ projectId, traceId, name, alt }: { projectId: number; traceId: number; name: string; alt: string }) {
  const dark = false; // the interface is always white with green
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setUrl(null);
    setError(null);
    twin
      .figure(projectId, traceId, name, dark ? "dark" : "light")
      .then((u) => live && setUrl(u))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [projectId, traceId, name, dark]);

  if (error) return <p className="note" role="status">This figure is not available for this setup: {error}.</p>;
  if (!url) return <div className="mpl-fig mpl-loading" aria-busy="true">Drawing the figure…</div>;
  return (
    <figure className="mpl">
      <img className="mpl-fig" src={url} alt={alt} />
      <a className="link mpl-save" href={url} download={`${name}.png`}>Save as PNG</a>
    </figure>
  );
}