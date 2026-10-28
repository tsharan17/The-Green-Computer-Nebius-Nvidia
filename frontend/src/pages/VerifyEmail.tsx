import { useEffect, useState } from "react";
import { auth } from "../api";
import Logo from "../components/Logo";

/** Opened from the confirmation email; works whether or not the person is signed in. */
export default function VerifyEmail({ token, onDone }: { token: string; onDone: () => void }) {
  const [state, setState] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    auth.verifyEmail(token)
      .then((r) => { setState({ ok: true, text: `${r.email} is confirmed.` }); onDone(); })
      .catch((e) => setState({ ok: false, text: e instanceof Error ? e.message : String(e) }));
  }, [token, onDone]);
  return (
    <main className="verify-email">
      <div className="brandmark"><Logo /> <strong>The Green Computer</strong></div>
      <h1>Confirm your email</h1>
      {state === null ? <p aria-busy="true">Checking the link…</p> : (
        <p className={state.ok ? "note-ok" : "error"} role={state.ok ? "status" : "alert"}>{state.text}</p>
      )}
      <p><a href="#/">Go to your workspace</a></p>
    </main>
  );
}
