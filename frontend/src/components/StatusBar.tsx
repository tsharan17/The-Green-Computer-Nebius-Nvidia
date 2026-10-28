import { useEffect, useState } from "react";

interface Health {
  version: string;
  demo_mode: boolean;
  agent: { configured: boolean };
}

/** Footer: what is connected, so nobody mistakes a prediction for a live measurement. */
export default function StatusBar() {
  const [h, setH] = useState<Health | null>(null);
  useEffect(() => {
    fetch("/api/v1/health").then((r) => r.json()).then(setH).catch(() => setH(null));
  }, []);
  return (
    <footer className="statusbar">
      <span><i className={`dot ${h ? "on" : ""}`} /> {h ? `Engine online · v${h.version}` : "Engine unreachable"}</span>
      <span><i className={`dot ${h?.agent.configured ? "on" : ""}`} /> Green Agent {h?.agent.configured ? "connected" : "off (no key)"}</span>
      {h?.demo_mode && <span><i className="dot warn" /> Public demo mode</span>}
    </footer>
  );
}
