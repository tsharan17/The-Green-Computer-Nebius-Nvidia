import { useCallback, useEffect, useState } from "react";
import { auth, workspace } from "../api";
import type { Me, SessionItem } from "../types";
import { SignInSecurity, WorkspaceDataPanel } from "../components/SecurityPanels";
import { when } from "./WorkspacePage";

function msg(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

export default function AccountPage({ me, onChanged, onDeleted }: { me: Me; onChanged: () => void; onDeleted: () => void }) {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [members, setMembers] = useState<{ user_id: number; email: string; display_name: string; role: string }[]>([]);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [tokLabel, setTokLabel] = useState("");
  const [newToken, setNewToken] = useState<string | null>(null);
  const [teamName, setTeamName] = useState("");
  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setMemberRole] = useState("editor");
  const [delPw, setDelPw] = useState("");
  const [delConfirm, setDelConfirm] = useState("");
  const ws = me.workspaces.find((w) => w.id === me.active_workspace_id);

  const load = useCallback(async () => {
    try {
      setSessions(await auth.sessions());
      setMembers(await workspace.members());
    } catch (e) {
      setStatus({ ok: false, text: msg(e) });
    }
  }, []);
  useEffect(() => {
    load();
  }, [load, me.active_workspace_id]);

  async function run(fn: () => Promise<string>) {
    setStatus(null);
    try {
      setStatus({ ok: true, text: await fn() });
      load();
    } catch (e) {
      setStatus({ ok: false, text: msg(e) });
    }
  }

  return (
    <div className="account">
      <header className="ws-head">
        <h1>Account and workspace</h1>
        <p className="muted">Signed in as {me.user.email}. Account created {when(me.user.created_at)}.</p>
      </header>
      {status && <p className={status.ok ? "note-ok" : "error"} role={status.ok ? "status" : "alert"}>{status.text}</p>}

      <div className="ws-grid">
        <section className="panel" aria-labelledby="pw-h">
          <h2 id="pw-h">Change password</h2>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                const r = await auth.change(cur, next);
                setCur("");
                setNext("");
                return `Password changed. ${r.other_sessions_signed_out} other sign-in(s) were signed out.`;
              });
            }}
          >
            <label className="field">Current password<input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" required /></label>
            <label className="field">New password<input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={10} required /></label>
            <button className="primary">Change password</button>
          </form>
        </section>

        <section className="panel" aria-labelledby="tok-h">
          <h2 id="tok-h">API tokens</h2>
          <p className="hint">For the <code>gc</code> command line and scripts: send <code>Authorization: Bearer &lt;token&gt;</code>. A token acts as you; revoke it below when you no longer need it.</p>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                const t = await auth.token(tokLabel || "script", 90);
                setNewToken(t.token);
                setTokLabel("");
                return "Token created. Copy it now: it is not shown again.";
              });
            }}
          >
            <label className="field">Label<input value={tokLabel} onChange={(e) => setTokLabel(e.target.value)} placeholder="e.g. CI pipeline" maxLength={120} /></label>
            <button>Create a 90-day token</button>
          </form>
          {newToken && (
            <div className="note-info token-box">
              <code>{newToken}</code>
              <button onClick={() => navigator.clipboard?.writeText(newToken)}>Copy</button>
            </div>
          )}
        </section>
      </div>

      <section className="panel" aria-labelledby="ses-h">
        <h2 id="ses-h">Where you are signed in</h2>
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Kind</th><th>Device or label</th><th>Last used</th><th>Expires</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td>{s.kind === "api" ? "API token" : "Browser"}{s.current && <span className="badge"> this one</span>}</td>
                  <td className="muted clip">{s.label || s.user_agent || "–"}</td>
                  <td className="muted">{when(s.last_seen_at)}</td>
                  <td className="muted">{when(s.expires_at)}</td>
                  <td>{!s.current && <button className="link" onClick={() => run(async () => (await auth.revoke(s.id), "Signed out."))}>Sign out</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="ws-grid">
        <section className="panel" aria-labelledby="team-h">
          <h2 id="team-h">Team workspaces</h2>
          <p className="hint">Your personal workspace is private. To share projects, create a team workspace and add people who already have an account.</p>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                await workspace.create(teamName);
                setTeamName("");
                onChanged();
                return "Team workspace created. Switch to it from the workspace menu at the top.";
              });
            }}
          >
            <label className="field">Name<input value={teamName} onChange={(e) => setTeamName(e.target.value)} required maxLength={120} /></label>
            <button>Create team workspace</button>
          </form>
        </section>

        <section className="panel" aria-labelledby="mem-h">
          <h2 id="mem-h">Members of {ws?.name ?? "this workspace"}</h2>
          <ul className="plain-list">
            {members.map((m) => (
              <li key={m.user_id}>
                <span>{m.email}</span> <span className="badge">{m.role}</span>
                {me.role === "owner" && m.user_id !== me.user.id && (
                  <button className="link" onClick={() => run(async () => (await workspace.removeMember(m.user_id), "Member removed."))}>Remove</button>
                )}
              </li>
            ))}
          </ul>
          {me.role === "owner" && ws && !ws.personal && (
            <form
              className="inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  await workspace.addMember(memberEmail, memberRole);
                  setMemberEmail("");
                  return "Member added.";
                });
              }}
            >
              <label className="field">Email<input type="email" value={memberEmail} onChange={(e) => setMemberEmail(e.target.value)} required /></label>
              <label className="field">Role
                <select value={memberRole} onChange={(e) => setMemberRole(e.target.value)}>
                  <option value="editor">Editor: can change and analyse</option>
                  <option value="viewer">Viewer: read only</option>
                  <option value="owner">Owner: also manages members</option>
                </select>
              </label>
              <button>Add member</button>
            </form>
          )}
        </section>
      </div>

      <div className="ws-grid">
        <SignInSecurity me={me} onChanged={onChanged} />
        <WorkspaceDataPanel key={me.active_workspace_id} isOwner={me.role === "owner"} />
      </div>

      <section className="panel" aria-labelledby="terms-h">
        <h2 id="terms-h">Terms and privacy</h2>
        {me.terms?.accepted.length ? (
          <table className="tbl">
            <thead><tr><th scope="col">Document</th><th scope="col">Version accepted</th><th scope="col">When</th></tr></thead>
            <tbody>
              {me.terms.accepted.map((a) => (
                <tr key={`${a.document}-${a.version}`}>
                  <td><a href={`#/legal/${a.document}`}>{a.document === "terms" ? "Terms and Conditions" : a.document === "privacy" ? "Privacy Policy" : a.document}</a></td>
                  <td className="num">{a.version}</td>
                  <td>{new Date(a.accepted_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No acceptance is recorded for this account (it was created before terms were introduced).</p>
        )}
        <p className="hint"><a href="#/legal/data">What happens to your data</a> · <a href="#/legal/privacy">Privacy Policy</a></p>
      </section>

      <section className="panel danger" aria-labelledby="del-h">
        <h2 id="del-h">Delete account</h2>
        <p>
          This permanently deletes your account, every workspace where you are the only member, and all projects, versions, analyses,
          uploaded files, measurements and exports in them. In shared workspaces you are removed and your name is taken off records;
          the workspace passes to another member. This cannot be undone.
        </p>
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            run(async () => {
              await auth.deleteAccount(delPw, delConfirm);
              onDeleted();
              return "Account deleted.";
            });
          }}
        >
          <label className="field">Password<input type="password" value={delPw} onChange={(e) => setDelPw(e.target.value)} autoComplete="current-password" required /></label>
          <label className="field">Type DELETE to confirm<input value={delConfirm} onChange={(e) => setDelConfirm(e.target.value)} required /></label>
          <button className="danger-btn" disabled={delConfirm !== "DELETE" || !delPw}>Delete my account</button>
        </form>
      </section>
    </div>
  );
}
