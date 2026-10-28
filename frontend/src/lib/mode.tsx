import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { prefs } from "../api";

/** Display density. Changes what is shown first, never what data is available. */
export type Mode = "simple" | "engineering";

interface ModeState {
  mode: Mode;
  setMode: (m: Mode) => void;
  onboarded: boolean;
  setOnboarded: (v: boolean) => void;
}

const Ctx = createContext<ModeState>({ mode: "simple", setMode: () => undefined, onboarded: true, setOnboarded: () => undefined });

const KEY = "gc_mode";

export function ModeProvider({ children, signedIn }: { children: React.ReactNode; signedIn: boolean }) {
  const [mode, setModeState] = useState<Mode>(() => {
    try {
      return localStorage.getItem(KEY) === "engineering" ? "engineering" : "simple";
    } catch {
      return "simple";
    }
  });
  const [onboarded, setOnboardedState] = useState(true);

  useEffect(() => {
    if (!signedIn) return;
    prefs
      .get()
      .then((p) => {
        setModeState(p.mode);
        setOnboardedState(p.onboarded);
      })
      .catch(() => undefined);
  }, [signedIn]);

  useEffect(() => {
    document.documentElement.dataset.mode = mode;
  }, [mode]);

  const setMode = useCallback((m: Mode) => {
    setModeState(m);
    try {
      localStorage.setItem(KEY, m);
    } catch {
      /* storage unavailable: the choice still applies to this page */
    }
    prefs.set({ mode: m }).catch(() => undefined);
  }, []);
  const setOnboarded = useCallback((v: boolean) => {
    setOnboardedState(v);
    prefs.set({ onboarded: v }).catch(() => undefined);
  }, []);

  return <Ctx.Provider value={{ mode, setMode, onboarded, setOnboarded }}>{children}</Ctx.Provider>;
}

export function useMode() {
  return useContext(Ctx);
}
