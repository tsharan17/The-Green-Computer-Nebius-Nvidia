import Logo from "../components/Logo";
import { useEffect, useState } from "react";
import { auth } from "../api";
import { navigate } from "../router";
import type { AuthConfig, Me } from "../types";

type Mode = "signin" | "signup" | "forgot" | "reset";

const COPY: Record<Mode, { title: string; lead: string; action: string }> = {
  signin: { title: "Sign in", lead: "Pick up your projects where you left them.", action: "Sign in" },
  signup: {
    title: "Create your account",
    lead: "Your projects, uploads and measurements stay private to your workspace.",
    action: "Create account",
  },
  forgot: { title: "Reset your password", lead: "We will send a link that works for one hour.", action: "Send reset link" },
  reset: { title: "Choose a new password", lead: "Every device signed in to this account will be signed out.", action: "Save new password" },
};

export default function AuthPage({ mode, token, onSignedIn }: { mode: Mode; token?: string; onSignedIn: (me: Me) => void }) {
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ message: string; link?: string; note?: string } | null>(null);
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [accepted, setAccepted] = useState(false);

  useEffect(() => {
    auth.config().then(setCfg).catch(() => setCfg(null));
  }, []);
  useEffect(() => {
    setError(null);
    setDone(null);
    setPassword("");
    setMfaToken(null);
  }, [mode]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "signin" && mfaToken) onSignedIn(await auth.signinMfa(mfaToken, code));
      else if (mode === "signin") {
        const r = await auth.signin(email, password);
        if ("mfa_required" in r) {
          setMfaToken(r.mfa_token);
          setPassword("");
        } else onSignedIn(r);
      }
      else if (mode === "signup") {
        if (!accepted) throw new Error("Please read and accept the Terms and Conditions and the Privacy Policy.");
        onSignedIn(await auth.signup(email, password, name, accepted));
      }
      else if (mode === "forgot") {
        const r = await auth.forgot(email);
        setDone({ message: r.message, link: r.dev_reset_url, note: r.dev_note });
      } else {
        await auth.reset(token || "", password);
        setDone({ message: "Password changed. Sign in with the new password." });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const c = COPY[mode];
  const minLen = cfg?.password_min_length ?? 10;
  return (
    <div className="auth">
      <section className="auth-intro" aria-label="About The Green Computer">
        <div className="brandmark">
          <Logo />
          <strong>The Green Computer</strong>
        </div>
        <h1>Will your AI model run well on your hardware? Find out before you build it.</h1>
        <ol className="auth-steps">
          <li><strong>You choose</strong> a model and a board (or upload them) and say what matters most.</li>
          <li><strong>We predict</strong> speed, memory, power, heat and energy across thousands of settings.</li>
          <li><strong>You get</strong> a plain verdict, the biggest bottleneck and the one change to make.</li>
        </ol>
        <figure className="auth-example" aria-label="Illustration of a result">
          <figcaption>What a result looks like (illustration)</figcaption>
          <p className="ae-row"><span>Today</span> <span className="outcome bad"><span aria-hidden className="outcome-icon">✕</span> Fails</span>
            <span aria-hidden>→</span><span>With the plan</span> <span className="outcome ok"><span aria-hidden className="outcome-icon">✓</span> Passes</span></p>
          <p className="ae-line">YOLOv8s on a Jetson Orin NX gets too hot at 40 °C with passive cooling.</p>
          <p className="ae-line"><strong>Do this:</strong> switch to INT8 and the 25 W power mode.</p>
          <p className="hint">Predicted, with a range. Measured once you run it on the device.</p>
        </figure>
        <p className="hint">Runs fully on your own server. GPU and AI-assistant integrations are optional.</p>
      </section>

      <section className="auth-card" aria-labelledby="auth-title">
        <h2 id="auth-title">{c.title}</h2>
        <p className="muted">{c.lead}</p>
        {done ? (
          <div className="auth-done" role="status">
            <p>{done.message}</p>
            {done.link && (
              <div className="note-info">
                <p>{done.note}</p>
                <a href={done.link.replace(/^https?:\/\/[^/]+\//, "/")}>Open the reset link</a>
              </div>
            )}
            <button className="primary" onClick={() => navigate("/signin")}>Back to sign in</button>
          </div>
        ) : (
          <form onSubmit={submit} noValidate>
            {mode === "signup" && (
              <label className="field">
                Your name (optional)
                <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={120} />
              </label>
            )}
            {mfaToken && (
              <label className="field">
                Code from your authenticator app
                <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" autoFocus required maxLength={20}
                  aria-describedby="mfa-hint" />
                <span id="mfa-hint" className="hint">Lost your phone? Type one of your recovery codes instead.</span>
              </label>
            )}
            {mode !== "reset" && !mfaToken && (
              <label className="field">
                Email
                <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" autoFocus />
              </label>
            )}
            {mode !== "forgot" && !mfaToken && (
              <label className="field">
                {mode === "reset" ? "New password" : "Password"}
                <input
                  type="password"
                  required
                  minLength={mode === "signin" ? undefined : minLen}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={mode === "signin" ? "current-password" : "new-password"}
                  aria-describedby={mode !== "signin" ? "pw-hint" : undefined}
                />
                {mode !== "signin" && (
                  <span id="pw-hint" className="hint">At least {minLen} characters. A short sentence works well.</span>
                )}
              </label>
            )}
            {mode === "signup" && (
              <label className="check-row consent">
                <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} required aria-describedby="consent-hint" />
                <span>
                  I have read and accept the <a href="#/legal/terms" target="_blank" rel="noopener">Terms and Conditions</a> and the{" "}
                  <a href="#/legal/privacy" target="_blank" rel="noopener">Privacy Policy</a>.
                </span>
              </label>
            )}
            {mode === "signup" && (
              <p id="consent-hint" className="hint">
                Your uploads stay yours: they are processed only to give you results. Nothing is sent to an AI provider unless you use an AI feature.
              </p>
            )}
            {error && <p className="error" role="alert">{error}</p>}
            <button className="primary wide" disabled={busy} type="submit">
              {busy ? "Working…" : mfaToken ? "Continue" : c.action}
            </button>
          </form>
        )}
        <div className="auth-links">
          {mode === "signin" && (
            <>
              {cfg?.signup_allowed !== false && <a href="#/signup">Create an account</a>}
              <a href="#/forgot">Forgot your password?</a>
            </>
          )}
          {mode !== "signin" && <a href="#/signin">I already have an account</a>}
        </div>
        <nav className="legal-links" aria-label="Legal">
          <a href="#/legal/terms">Terms</a><a href="#/legal/privacy">Privacy</a><a href="#/legal/acceptable-use">Acceptable use</a>
        </nav>
        {cfg && cfg.environment !== "production" && cfg.email_delivery === "console" && mode === "forgot" && !done && (
          <p className="hint">This server has no mail delivery configured, so reset links are written to the server log.</p>
        )}
      </section>
    </div>
  );
}
