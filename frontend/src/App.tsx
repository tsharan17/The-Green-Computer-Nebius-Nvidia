import VerifyEmail from "./pages/VerifyEmail";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, auth, history, setWorkspace } from "./api";
import AgentPanel from "./components/AgentPanel";
import AgentStageCard from "./components/AgentStageCard";
import DecisionBar from "./components/DecisionBar";
import ErrorBoundary from "./components/ErrorBoundary";
import ExecutionPanel from "./components/ExecutionPanel";
import ExportPanel from "./components/ExportPanel";
import GlobalSearch from "./components/GlobalSearch";
import { ErrorNote } from "./components/Help";
import HistoryPanel from "./components/HistoryPanel";
import Logo from "./components/Logo";
import PlanBuilder from "./components/PlanBuilder";
import SavedPlans from "./components/SavedPlans";
import StatusBar from "./components/StatusBar";
import { ModeProvider, useMode } from "./lib/mode";
import AccountPage from "./pages/AccountPage";
import AuthPage from "./pages/AuthPage";
import CapabilitiesPage from "./pages/CapabilitiesPage";
import ConstraintsPage from "./pages/ConstraintsPage";
import InputPage from "./pages/InputPage";
import ResultsPage from "./pages/ResultsPage";
import StartGuide from "./pages/StartGuide";
import SummaryPage from "./pages/SummaryPage";
import VerifyPage from "./pages/VerifyPage";
import WorkspacePage from "./pages/WorkspacePage";
import LegalPage, { LegalLinks } from "./pages/LegalPage";
import CoolingFigures from "./workflow/CoolingFigures";
import PipelineStrip from "./workflow/PipelineStrip";
import RecommendationCatalogue from "./workflow/RecommendationCatalogue";
import RecommendationEngine from "./workflow/RecommendationEngine";
import CoolingOptions from "./components/CoolingOptions";
import VerificationPanel from "./workflow/VerificationPanel";
import { AREAS, type AreaId, navigate, useRoute } from "./router";
import { SIDE_VIEWS, STAGES as FLOW_STAGES, STEPS, hrefOf, locate, neighbours } from "./flow";
import { StepFooter, StepHeader, SubSteps } from "./components/StepNav";
import MemoryPage from "./twin/MemoryPage";
import SystemMapPage from "./twin/SystemMapPage";
import { EnergyPage, ThermalPage } from "./twin/ThermalEnergyPages";
import { TraceProvider } from "./twin/TraceBar";
import TimelinePage from "./twin/TimelinePage";
import type { HardwareProfile, Me, ModelProfile, OptimisationResult, ProjectOut, ProjectSpec } from "./types";

const WS_KEY = "gc_workspace";



function readWs(): number | null {
  try {
    return Number(localStorage.getItem(WS_KEY)) || null;
  } catch {
    return null;
  }
}

export default function App() {
  const route = useRoute();
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  const loadMe = useCallback(async () => {
    try {
      const m = await auth.me();
      const stored = readWs();
      const active = m.workspaces.find((w) => w.id === stored) ? stored : m.active_workspace_id;
      setWorkspace(active);
      setMe({ ...m, active_workspace_id: active, role: m.workspaces.find((w) => w.id === active)?.role ?? null });
    } catch {
      setWorkspace(null);
      setMe(null);
    }
  }, []);

  useEffect(() => {
    loadMe();
    const out = () => setMe(null);
    window.addEventListener("gc:unauthorized", out);
    return () => window.removeEventListener("gc:unauthorized", out);
  }, [loadMe]);

  const page = route.parts[0] ?? "";
  if (page === "reset") return <AuthPage mode="reset" token={route.query.get("token") ?? ""} onSignedIn={() => loadMe()} />;
  if (page === "verify-email") return <VerifyEmail token={route.query.get("token") ?? ""} onDone={loadMe} />;
  if (page === "legal") return <LegalPage doc={route.parts[1] ?? "terms"} />;
  if (me === undefined) {
    return (
      <div className="boot" aria-busy="true" aria-live="polite">
        <Logo /> Loading The Green Computer…
      </div>
    );
  }
  if (me === null) {
    const mode = page === "signup" || page === "forgot" ? page : "signin";
    return (
      <AuthPage
        mode={mode}
        onSignedIn={(m) => {
          setWorkspace(m.active_workspace_id);
          setMe(m);
          navigate(mode === "signup" ? "/start" : "/");
        }}
      />
    );
  }
  return (
    <ModeProvider signedIn>
      <Shell
        me={me}
        route={route}
        switchWorkspace={(id) => {
          try {
            localStorage.setItem(WS_KEY, String(id));
          } catch {
            /* storage unavailable: the choice lasts for this tab only */
          }
          setWorkspace(id);
          setMe({ ...me, active_workspace_id: id, role: me.workspaces.find((w) => w.id === id)?.role ?? null });
          navigate("/");
        }}
        reloadMe={loadMe}
        signedOut={() => {
          setWorkspace(null);
          setMe(null);
          navigate("/signin");
        }}
      />
    </ModeProvider>
  );
}

function ModeSwitch() {
  const { mode, setMode } = useMode();
  return (
    <div className="seg mode-switch" role="radiogroup" aria-label="Display">
      <button role="radio" aria-checked={mode === "simple"} className={mode === "simple" ? "on" : ""} onClick={() => setMode("simple")}
        title="Plain answers first; engineering detail one click away">Simple</button>
      <button role="radio" aria-checked={mode === "engineering"} className={mode === "engineering" ? "on" : ""} onClick={() => setMode("engineering")}
        title="Engineering detail open by default">Engineering</button>
    </div>
  );
}

function Shell({
  me,
  route,
  switchWorkspace,
  reloadMe,
  signedOut,
}: {
  me: Me;
  route: ReturnType<typeof useRoute>;
  switchWorkspace: (id: number) => void;
  reloadMe: () => void;
  signedOut: () => void;
}) {
  const { mode } = useMode();
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [devices, setDevices] = useState<HardwareProfile[]>([]);
  const [libError, setLibError] = useState<unknown>(null);
  const [agentOpen, setAgentOpen] = useState(false);
  const [agentAsk, setAgentAsk] = useState<{ text: string; n: number } | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const refreshLibrary = useCallback(async () => {
    try {
      const [m, d] = await Promise.all([api.models(), api.devices()]);
      setModels(m);
      setDevices(d);
      setLibError(null);
    } catch (e) {
      setLibError(e);
    }
  }, []);
  useEffect(() => {
    refreshLibrary();
  }, [refreshLibrary, me.active_workspace_id]);

  const [p0, p1, p2] = route.parts;
  const projectId = p0 === "p" && p1 ? Number(p1) : null;
  const area = (AREAS.some((a) => a.id === p2) ? p2 : null) as AreaId | null;
  const canWrite = me.role === "owner" || me.role === "editor";
  const here = locate(area, route.query.get("tab"));
  const railLink = (key: string, title: string, href: string, on: boolean, optional = false) => (
    <a key={key} className={`rail-item ${on ? "on" : ""}`} href={href} aria-current={on ? "page" : undefined}>
      <span className="rail-dot" aria-hidden />
      <span className="rail-text"><strong>{title}{optional && <span className="rail-opt"> (optional)</span>}</strong></span>
    </a>
  );
  // One group per stage, numbered like the stepper at the top; optional views sit under "More".
  const stageGroup = (n: number) => {
    const steps = STEPS.filter((x) => x.stage === n);
    const views = SIDE_VIEWS.filter((v) => steps.some((x) => x.id === v.parent));
    return (
      <div className={`rail-group ${here.step?.stage === n ? "on" : ""}`} key={n}>
        <span className="rail-title"><span className="rail-n" aria-hidden>{n}</span>{FLOW_STAGES[n - 1]}</span>
        {steps.map((x) => railLink(x.id, x.title, hrefOf(projectId!, x), here.step?.id === x.id && !here.side, x.optional))}
        {views.length > 0 && (
          <details className="nav-more" open={mode === "engineering" || views.some((v) => v.id === here.side?.id)}>
            <summary>More detail ({views.length})</summary>
            {views.map((v) => railLink(v.id, v.title, hrefOf(projectId!, v), here.side?.id === v.id))}
          </details>
        )}
      </div>
    );
  };

  async function signOut() {
    try {
      await auth.signout();
    } finally {
      signedOut();
    }
  }

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <aside className="sidebar" aria-label="Main navigation">
        <a className="brand" href="#/" aria-label="The Green Computer, workspace">
          <Logo />
          <span>
            <strong>The Green Computer</strong>
            <small>AI and hardware co-design</small>
          </span>
        </a>
        <nav className="rail" aria-label="Workflow">
          <a className={`rail-item ${!projectId && !p0 ? "on" : ""}`} href="#/" aria-current={!projectId && !p0 ? "page" : undefined}>
            <span className="rail-dot" aria-hidden />
            <span className="rail-text"><strong>Workspace</strong><small>Projects and onboarding</small></span>
          </a>
          {projectId !== null && FLOW_STAGES.map((_, i) => stageGroup(i + 1))}
        </nav>
        {projectId !== null && (
          <button className="agent-btn" onClick={() => setAgentOpen(true)}>
            Ask the assistant
            <small>Green Agent, NVIDIA Nemotron on Nebius</small>
          </button>
        )}
        <a className={`cap-link ${p0 === "capabilities" ? "on" : ""}`} href="#/capabilities">What this installation can do</a>
        <StatusBar />
        <LegalLinks />
      </aside>

      <div className="content">
        <header className="topbar">
          <label className="ws-switch">
            <span className="sr-only">Workspace</span>
            <select value={me.active_workspace_id ?? ""} onChange={(e) => switchWorkspace(Number(e.target.value))}>
              {me.workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}{w.role !== "owner" ? ` (${w.role})` : ""}
                </option>
              ))}
            </select>
          </label>
          <GlobalSearch />
          <ModeSwitch />
          <div className="account-menu">
            <button aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((o) => !o)}>
              {me.user.display_name || me.user.email}
            </button>
            {menuOpen && (
              <div className="menu" role="menu" onClick={() => setMenuOpen(false)}>
                <a role="menuitem" href="#/account">Account and workspace</a>
                <button role="menuitem" onClick={signOut}>Sign out</button>
              </div>
            )}
          </div>
        </header>

        <main id="main" tabIndex={-1}>
          {me.user.verification_required && !me.user.email_verified && (
            <p className="verify-banner" role="status">
              <span aria-hidden>!</span> Confirm your email to make changes: open the link we sent to {me.user.email}, or <a href="#/account">send a new one</a>.
            </p>
          )}
          {!!me.terms?.outstanding.length && <TermsBanner onAccepted={reloadMe} docs={me.terms.outstanding} />}
          <ErrorNote error={libError} onRetry={refreshLibrary} />
          <ErrorBoundary>
            {p0 === "account" ? (
              <AccountPage me={me} onChanged={reloadMe} onDeleted={signedOut} />
            ) : p0 === "capabilities" ? (
              <CapabilitiesPage />
            ) : p0 === "start" ? (
              <StartGuide models={models} devices={devices} onModelAdded={(m) => setModels((prev) => [...prev.filter((x) => x.id !== m.id), m])} />
            ) : projectId !== null ? (
              <ProjectView
                key={`${me.active_workspace_id}-${projectId}`}
                projectId={projectId}
                area={area}
                query={route.query}
                models={models}
                devices={devices}
                canWrite={canWrite}
                setModels={setModels}
                refreshLibrary={refreshLibrary}
                agentOpen={agentOpen}
                closeAgent={() => {
                  setAgentOpen(false);
                  setAgentAsk(null); // a picked question is asked once, not again when the drawer reopens
                }}
                agentAsk={agentAsk}
                askAgent={(text) => {
                  setAgentAsk((a) => ({ text, n: (a?.n ?? 0) + 1 }));
                  setAgentOpen(true);
                }}
              />
            ) : (
              <WorkspacePage me={me} models={models} devices={devices} />
            )}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

function TermsBanner({ docs, onAccepted }: { docs: string[]; onAccepted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const names: Record<string, string> = { terms: "Terms and Conditions", privacy: "Privacy Policy" };
  return (
    <div className="verify-banner" role="status">
      <span aria-hidden>!</span>
      <span>
        The {docs.map((d) => names[d] ?? d).join(" and ")} changed. Please read{" "}
        {docs.map((d, i) => <span key={d}>{i > 0 && " and "}<a href={`#/legal/${d}`}>{names[d] ?? d}</a></span>)} and accept to continue.
      </span>
      <button disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          await auth.acceptTerms();
          onAccepted();
        } catch (e) {
          setError(e);
        } finally {
          setBusy(false);
        }
      }}>{busy ? "Saving…" : "I have read and accept"}</button>
      <ErrorNote error={error} />
    </div>
  );
}

function ProjectView({
  projectId,
  area,
  query,
  models,
  devices,
  canWrite,
  setModels,
  refreshLibrary,
  agentOpen,
  closeAgent,
  agentAsk,
  askAgent,
}: {
  projectId: number;
  area: AreaId | null;
  query: URLSearchParams;
  models: ModelProfile[];
  devices: HardwareProfile[];
  canWrite: boolean;
  setModels: React.Dispatch<React.SetStateAction<ModelProfile[]>>;
  refreshLibrary: () => Promise<void>;
  agentOpen: boolean;
  closeAgent: () => void;
  agentAsk: { text: string; n: number } | null;
  askAgent: (question: string) => void;
}) {
  const [project, setProject] = useState<ProjectOut | null>(null);
  const [spec, setSpec] = useState<ProjectSpec | null>(null);
  const [res, setRes] = useState<OptimisationResult | null>(null);
  const [resAt, setResAt] = useState<string | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notFound, setNotFound] = useState(false);
  const [verifyKey, setVerifyKey] = useState(0);
  const [plansKey, setPlansKey] = useState(0);
  const qAdd = query.get("add");
  // The address is the one source of truth for where the user is: the stepper, the side navigation,
  // the sub-step row and Back / Next all read it.
  const here = locate(area, query.get("tab"));
  const curTab = here.side?.tab ?? here.step?.tab ?? (area === "recommendations" ? "engine" : "run");
  const recTab = curTab;
  const tab = curTab;

  const load = useCallback(async () => {
    try {
      const p = await api.project(projectId);
      setProject(p);
      setSpec(p.spec);
      // Results survive a reload: show the latest stored analysis of this project.
      const runs = await history.runs(projectId);
      const last = runs.find((r) => r.kind === "optimise");
      if (last) {
        const full = await history.run<OptimisationResult>(projectId, last.id);
        setRes(full.result);
        setResAt(full.created_at);
        setRunId(last.id);
      }
      setLoaded(true);
    } catch (e) {
      if ((e as { status?: number }).status === 404) setNotFound(true);
      else setError(e);
    }
  }, [projectId]);
  useEffect(() => {
    load();
  }, [load]);

  // A bare project link opens the answer when there is one, else the inputs.
  useEffect(() => {
    if (loaded && area === null) navigate(`/p/${projectId}/${res ? "summary" : "input"}`);
  }, [loaded, area, res, projectId]);

  const dirty = useMemo(() => !!project && !!spec && JSON.stringify(project.spec) !== JSON.stringify(spec), [project, spec]);
  const stale = dirty && !!res;

  async function save() {
    if (!spec) return;
    setBusy(true);
    setError(null);
    try {
      setProject(await api.save(projectId, spec));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function analyse() {
    if (!spec) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await api.save(projectId, spec);
      setProject(saved);
      setRes(await api.optimise(projectId));
      setResAt(new Date().toISOString());
      const runs = await history.runs(projectId);
      setRunId(runs.find((r) => r.kind === "optimise")?.id ?? null);
      navigate(`/p/${projectId}/summary`);
      window.scrollTo({ top: 0 });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  if (notFound) {
    return (
      <section className="panel">
        <h1>Project not found</h1>
        <p>It may have been deleted, or it belongs to another workspace. Switch workspace at the top, or go back to your projects.</p>
        <a className="btn-link" href="#/">Back to projects</a>
      </section>
    );
  }
  if (!spec || !project) {
    return (
      <div className="panel skeleton" aria-busy="true">
        <div className="sk-line w60" /><div className="sk-line w90" /><div className="sk-line w75" />
        <ErrorNote error={error} onRetry={load} />
      </div>
    );
  }

  const model = models.find((m) => m.id === spec.model_id);
  const device = devices.find((d) => d.id === spec.device_id);
  const needsResult = (
    <section className="panel empty-state">
      <h2>No analysis yet</h2>
      <p>The inputs are set. Run the analysis to get a verdict, the biggest bottleneck and a recommended change; it takes a few seconds.</p>
      <div className="actions">
        <button className="primary" disabled={busy || !canWrite} onClick={analyse}>{busy ? "Analysing…" : "Analyse now"}</button>
        <a className="btn-link" href={`#/p/${projectId}/input`}>Review the inputs first</a>
      </div>
    </section>
  );
  const nextStep = here.step ? neighbours(here.step).next : null;
  const goLegacy = (p: string) =>
    navigate(`/p/${projectId}/${({ start: "input", results: "recommendations", explore: "energy", export: "verify", verify: "verify" } as Record<string, string>)[p] ?? "input"}`);

  return (
    <TraceProvider projectId={projectId} version={project.version} hasBest={!!res?.best} runKey={runId}>
      <div className="project-head">
        <div>
          <h1>{spec.name}</h1>
          <p className="muted">
            {model?.name ?? spec.model_id} on {device?.name ?? spec.device_id}, inputs version {project.version}
          </p>
        </div>
      </div>
      <PipelineStrip projectId={projectId} refreshKey={`${project.version}-${runId}-${plansKey}-${area}`} current={here.step?.stage ?? null} />
      <DecisionBar decide={area === "recommendations" || area === "verify"} project={project} res={res} stale={stale || (!!res && dirty)} busy={busy} canWrite={canWrite} model={model} device={device} onAnalyse={analyse}
        next={nextStep ? { label: `Next: ${nextStep.title}`, href: hrefOf(projectId, nextStep) } : null} />
      <ErrorNote error={error} onRetry={() => setError(null)} />
      {here.step && <StepHeader projectId={projectId} step={here.step} side={here.side} />}
      {here.step && (
        <AgentStageCard step={here.step} model={model?.name ?? spec.model_id} device={device?.name ?? spec.device_id}
          spec={spec} res={res} canWrite={canWrite} onAsk={askAgent} />
      )}
      {here.step && <SubSteps projectId={projectId} step={here.step} side={here.side} />}

      {area === "summary" && (res ? <SummaryPage res={res} spec={spec} projectId={projectId} runId={runId} version={project.version} /> : needsResult)}
      {area === "input" && (
        <InputPage
          spec={spec}
          setSpec={(s) => setSpec(s)}
          models={models}
          devices={devices}
          projectId={projectId}
          canWrite={canWrite}
          onModelAdded={(m) => {
            setModels((prev) => [...prev.filter((x) => x.id !== m.id), m]);
            setSpec((s) => (s ? { ...s, model_id: m.id } : s));
          }}
          onDevicesChanged={refreshLibrary}
        />
      )}
      {area === "check" && (
        <>
          <VerificationPanel projectId={projectId} refreshKey={`${project.version}-${dirty}`} />
          {dirty && <p className="note-warn" role="status">You have unsaved changes; the report shows the saved version {project.version}. Save to check the new inputs.</p>}
          <div className="actions">
            <button disabled={busy || !canWrite || !dirty} onClick={save}>Save changes</button>
          </div>
        </>
      )}
      {area === "constraints" && <ConstraintsPage spec={spec} setSpec={(s) => setSpec(s)} device={device} canWrite={canWrite} />}
      {(area === "input" || area === "constraints") && (
        <div className="actionbar" role="region" aria-label="Save and analyse">
          <div className="ab-summary">
            <span>
              <strong>{model?.name ?? spec.model_id}</strong> on <strong>{device?.name ?? spec.device_id}</strong>
            </span>
            <span className="muted">
              {spec.workload.fps} inputs/s, at most {spec.constraints.max_power_w} W, {spec.workload.ambient_c} °C ambient
              {dirty ? ", unsaved changes" : ""}
            </span>
            {!canWrite && <small className="ab-caveat">Your role in this workspace is read-only.</small>}
          </div>
          <div className="ab-actions">
            {area === "constraints" && <a className="btn-link" href={hrefOf(projectId, STEPS[0])}>← Back: {STEPS[0].title}</a>}
            {nextStep && <a className="btn-link" href={hrefOf(projectId, nextStep)}>Next: {nextStep.title} →</a>}
            <button disabled={busy || !canWrite || !dirty} onClick={save}>Save as version {project.version + (dirty ? 1 : 0)}</button>
            <button className="primary big" disabled={busy || !canWrite} onClick={analyse}>{busy ? "Analysing…" : "Analyse"}</button>
          </div>
        </div>
      )}
      {/* The cooling comparison needs only the inputs, not an analysis. */}
      {area === "recommendations" && recTab === "cooling" && <CoolingOptions projectId={projectId} which="baseline" />}
      {area === "recommendations" && recTab !== "cooling" &&
        (res ? (
          <>
            {recTab === "engine" && <RecommendationEngine projectId={projectId} version={project.version} runKey={runId} canWrite={canWrite} />}
            {recTab === "results" && <ResultsPage res={res} spec={spec} go={goLegacy} />}
            {recTab === "catalogue" && <RecommendationCatalogue projectId={projectId} canWrite={canWrite} refreshKey={`${runId}-${plansKey}`} />}
            {recTab === "plan" && (
              <PlanBuilder key={`${resAt}-${qAdd ?? ""}`} projectId={projectId} canWrite={canWrite} initialAdd={qAdd} onSaved={() => setPlansKey((k) => k + 1)} />
            )}
            {recTab === "saved" && <SavedPlans projectId={projectId} refreshKey={plansKey} />}
          </>
        ) : (
          needsResult
        ))}
      {area === "system-map" && <SystemMapPage projectId={projectId} version={project.version} hasBest={!!res?.best} />}
      {area === "timeline" && <TimelinePage projectId={projectId} version={project.version} hasBest={!!res?.best} />}
      {area === "memory" && <MemoryPage projectId={projectId} version={project.version} hasBest={!!res?.best} />}
      {area === "thermal" && (
        <>
          <ThermalPage projectId={projectId} version={project.version} hasBest={!!res?.best} />
          <CoolingFigures projectId={projectId} version={project.version} />
        </>
      )}
      {area === "energy" && <EnergyPage projectId={projectId} version={project.version} hasBest={!!res?.best} res={res} spec={spec} />}
      {area === "verify" && (
        <>
          {tab === "run" && <ExecutionPanel projectId={projectId} canWrite={canWrite} onMeasured={() => setVerifyKey((k) => k + 1)} />}
          {tab === "verify" && <VerifyPage key={verifyKey} devices={devices} models={models} onChanged={() => setRes(null)} />}
          {tab === "history" && <HistoryPanel projectId={projectId} canWrite={canWrite} onRestored={load} />}
          {tab === "export" && <ExportPanel projectId={projectId} canWrite={canWrite} />}
        </>
      )}
      {here.step && area !== "input" && area !== "constraints" && (
        <StepFooter
          projectId={projectId}
          step={here.step}
          side={here.side}
          nextAction={here.step.id === "check" || (here.step.id === "summary" && !res && !here.side)
            ? { label: busy ? "Analysing…" : "Analyse and continue", run: analyse, disabled: busy || !canWrite } : undefined}
        />
      )}
      {resAt && area !== "input" && <p className="foot">Showing the analysis stored {new Date(resAt).toLocaleString()}. Predictions come from a physics model with stated assumptions; measured values are labelled as measured.</p>}

      {agentOpen && <div className="scrim" onClick={closeAgent} />}
      <aside className={`drawer ${agentOpen ? "open" : ""}`} aria-hidden={!agentOpen} aria-label="Assistant">
        <button className="drawer-close" onClick={closeAgent} aria-label="Close the assistant">×</button>
        {agentOpen && <AgentPanel projectId={projectId} onProjectChanged={load} ask={agentAsk} />}
      </aside>
    </TraceProvider>
  );
}
