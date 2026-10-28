import { useEffect, useState } from "react";
import { auth, download, type RetentionRules, workspaceData } from "../api";
import type { Me } from "../types";
import { ErrorNote } from "./Help";

/** Email confirmation and optional two-step sign-in. */
export function SignInSecurity({ me, onChanged }: { me: Me; onChanged: () => void }) {
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const [devLink, setDevLink] = useState<string | null>(null);
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [offPw, setOffPw] = useState("");
  const [offCode, setOffCode] = useState("");
  const verified = me.user.email_verified;
  const mfa = me.user.mfa_enabled;

  async function act<T>(f: () => Promise<T>, then?: (v: T) => void) {
    setError(null);
    setNote(null);
    try {
      const v = await f();
      then?.(v);
    } catch (e) {
      setError(e);
    }
  }

  return (
    <section className="panel" aria-labelledby="sec-h">
      <h2 id="sec-h">Sign-in security</h2>
      <ErrorNote error={error} />
      {note && <p className="note-ok" role="status">{note}</p>}
      <h3>Email</h3>
      <p>
        <span className={`outcome ${verified ? "ok" : "warn"}`}><span aria-hidden className="outcome-icon">{verified ? "✓" : "!"}</span> {verified ? "confirmed" : "not confirmed yet"}</span>{" "}
        {me.user.email}
      </p>
      {!verified && (
        <>
          {me.user.verification_required && <p className="warn-text">This server needs a confirmed email before you can change anything.</p>}
          <button onClick={() => act(auth.resendVerification, (r) => { setNote(r.message); setDevLink(r.dev_verify_url ?? null); })}>Send a new confirmation link</button>
          {devLink && <p className="note-info">Development server without mail: <a href={devLink.replace(/^https?:\/\/[^/]+\//, "/")}>open the confirmation link</a>.</p>}
        </>
      )}

      <h3>Two-step sign-in</h3>
      {mfa ? (
        <>
          <p><span className="outcome ok"><span aria-hidden className="outcome-icon">✓</span> on</span> Signing in asks for a code from your authenticator app.</p>
          <form className="inline-form" onSubmit={(e) => { e.preventDefault(); act(() => auth.mfaDisable(offPw, offCode), () => { setOffPw(""); setOffCode(""); setNote("Two-step sign-in is off."); onChanged(); }); }}>
            <label className="field">Password<input type="password" value={offPw} onChange={(e) => setOffPw(e.target.value)} autoComplete="current-password" required /></label>
            <label className="field">Code or recovery code<input value={offCode} onChange={(e) => setOffCode(e.target.value)} autoComplete="one-time-code" required /></label>
            <button>Turn off</button>
          </form>
        </>
      ) : codes ? (
        <div className="pairing-box" role="status">
          <strong>Two-step sign-in is on. Save these recovery codes now; they are not shown again.</strong>
          <pre className="cmd">{codes.join("\n")}</pre>
          <p className="hint">Each works once if you lose your phone.</p>
          <button onClick={() => { setCodes(null); onChanged(); }}>I have saved them</button>
        </div>
      ) : setup ? (
        <div>
          <p>Add this key to an authenticator app (manual entry, time-based, 6 digits), then type the code it shows.</p>
          <p><code className="code-big">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code></p>
          <details className="why"><summary>Setup link for apps that accept one</summary><p><code>{setup.otpauth_uri}</code></p></details>
          <form className="inline-form" onSubmit={(e) => { e.preventDefault(); act(() => auth.mfaEnable(code), (r) => { setCodes(r.recovery_codes); setSetup(null); setCode(""); }); }}>
            <label className="field">Code from the app<input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={8} required /></label>
            <button className="primary">Turn on</button>
          </form>
        </div>
      ) : (
        <>
          <p className="hint">Optional. After your password, sign-in also asks for a code from an authenticator app on your phone.</p>
          <button onClick={() => act(auth.mfaSetup, setSetup)}>Set up two-step sign-in</button>
        </>
      )}
    </section>
  );
}

const DAY_HINT = "days (empty: keep)";

/** Owner tools: retention rules (with a dry run) and a complete export. */
export function WorkspaceDataPanel({ isOwner }: { isOwner: boolean }) {
  const [rules, setRules] = useState<RetentionRules | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    workspaceData.retention().then((r) => { setRules(r.rules); setLabels(r.labels); }).catch(setError);
  }, []);
  if (!rules) return null;

  async function act<T>(f: () => Promise<T>, then?: (v: T) => void) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      then?.(await f());
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  const total = preview ? Object.values(preview).reduce((a, b) => a + b, 0) : 0;

  return (
    <section className="panel" aria-labelledby="data-h">
      <h2 id="data-h">Workspace data</h2>
      <ErrorNote error={error} />
      {note && <p className="note-ok" role="status">{note}</p>}
      <h3>Keep data for</h3>
      <p className="hint">Off until set. Applying first shows what would be removed. The audit log always keeps at least 90 days.</p>
      <form onSubmit={(e) => { e.preventDefault(); act(() => workspaceData.setRetention(rules), (r) => { setRules(r.rules); setNote("Retention rules saved."); setPreview(null); }); }}>
        <div className="retention-grid">
          {Object.entries(labels).map(([k, label]) => (
            <label key={k} className="field">{label}
              <input type="number" min={k === "audit_days" ? 90 : 1} max={36500} value={rules[k] ?? ""} placeholder={DAY_HINT} disabled={!isOwner}
                onChange={(e) => setRules({ ...rules, [k]: e.target.value === "" ? null : Number(e.target.value) })} />
            </label>
          ))}
        </div>
        {isOwner && (
          <div className="actions">
            <button disabled={busy}>Save rules</button>
            <button type="button" disabled={busy} onClick={() => act(() => workspaceData.apply(true), (r) => setPreview(r.removed))}>Show what would be removed</button>
          </div>
        )}
      </form>
      {preview && (
        <div className="pairing-box" role="status">
          <p>{total === 0 ? "Nothing is old enough to remove." : `Would remove: ${Object.entries(preview).filter(([, n]) => n > 0).map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(", ")}.`}</p>
          {total > 0 && <button className="danger-btn" disabled={busy} onClick={() => window.confirm(`Permanently remove these ${total} items?`) && act(() => workspaceData.apply(false), (r) => { setPreview(null); setNote(`Removed ${Object.values(r.removed).reduce((a, b) => a + b, 0)} items.`); })}>Remove them now</button>}
        </div>
      )}
      <h3>Export everything</h3>
      <p className="hint">One zip with every record and stored file of this workspace and a hashed manifest. Secrets and other members' emails are left out.</p>
      {isOwner ? <button disabled={busy} onClick={() => act(() => download("/workspaces/current/export", "workspace-export.zip"), () => setNote("Export downloaded."))}>Download workspace export</button>
        : <p className="muted">Only a workspace owner can export or change retention.</p>}
    </section>
  );
}
