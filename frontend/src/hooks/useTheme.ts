import { useCallback, useSyncExternalStore } from "react";

export type ThemePreference = "light" | "night" | "system";
export type ResolvedTheme = "light" | "night";
export interface ThemeTransitionOrigin {
  x: number;
  y: number;
}

export interface ThemeTransitionDetail {
  theme: ResolvedTheme;
  fromTheme: ResolvedTheme;
  origin: ThemeTransitionOrigin;
  radius: number;
  duration: number;
}

interface ThemeSnapshot {
  preference: ThemePreference;
  resolvedTheme: ResolvedTheme;
}

const STORAGE_KEY = "loreweft-theme";
const listeners = new Set<() => void>();
let initialized = false;
let mediaQuery: MediaQueryList | null = null;
let snapshot: ThemeSnapshot = {
  preference: "system",
  resolvedTheme: "light",
};
let themeTransitionActive = false;

function readPreference(): ThemePreference {
  if (typeof window === "undefined") return "system";

  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "night" || saved === "system") return saved;

    // Migrate the unfinished legacy theme switch without changing user intent.
    const legacy = window.localStorage.getItem("theme");
    if (legacy === "dark") return "night";
    if (legacy === "light") return "light";
  } catch {
    return "system";
  }

  return "system";
}

function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference === "light" || preference === "night") return preference;
  return mediaQuery?.matches ? "night" : "light";
}

function applyTheme(next: ThemeSnapshot) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset.theme = next.resolvedTheme;
  root.dataset.themePreference = next.preference;
  root.style.colorScheme = next.resolvedTheme === "night" ? "dark" : "light";
  root.classList.toggle("theme-night", next.resolvedTheme === "night");
  root.classList.toggle("theme-light", next.resolvedTheme === "light");
}

function publish(preference: ThemePreference) {
  snapshot = { preference, resolvedTheme: resolveTheme(preference) };
  applyTheme(snapshot);
  listeners.forEach((listener) => listener());
}

function persistPreference(preference: ThemePreference) {
  try {
    window.localStorage.setItem(STORAGE_KEY, preference);
    window.localStorage.removeItem("theme");
  } catch {
    // Theme still applies for this session when storage is unavailable.
  }
}

function finishThemeTransition(root: HTMLElement) {
  themeTransitionActive = false;
  delete root.dataset.themeTransition;
  window.dispatchEvent(new Event("loreweft-theme-transition-end"));
  snapshot = { ...snapshot };
  listeners.forEach((listener) => listener());
}

interface ThemeNodeTransition {
  element: HTMLElement;
  delay: number;
  priority: number;
}

const THEME_TRANSITION_DURATION = 900;
const LINKED_NODE_LIMIT = 72;
const LINKED_NODE_SELECTOR = [
  ".app-shell",
  ".app-shell > aside",
  ".app-shell > main",
  ".app-shell aside",
  ".app-shell aside header",
  ".app-shell aside nav",
  ".app-shell aside button",
  ".app-shell aside a",
  ".app-shell aside svg",
  ".app-shell main > *",
  ".app-shell main header",
  ".app-shell main nav",
  ".app-shell main section",
  ".app-shell main article",
  ".app-shell main button",
  ".app-shell main a",
  ".app-shell main input",
  ".app-shell main textarea",
  ".app-shell main select",
  ".app-shell main [role='dialog']",
  ".app-shell main [data-theme-surface]",
  ".app-shell main .lobby-project-card",
  ".app-shell main .glass-panel",
  ".app-shell main .monet-card",
  ".app-shell main svg",
].join(",");

function collectThemeNodes(
  origin: ThemeTransitionOrigin,
  radius: number,
): ThemeNodeTransition[] {
  const seen = new Set<HTMLElement>();
  const nodes: ThemeNodeTransition[] = [];

  document.querySelectorAll<HTMLElement>(LINKED_NODE_SELECTOR).forEach((element) => {
    if (seen.has(element)) return;
    seen.add(element);
    const rect = element.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2 || rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) return;

    const nearestX = Math.max(rect.left, Math.min(origin.x, rect.right));
    const nearestY = Math.max(rect.top, Math.min(origin.y, rect.bottom));
    const distance = Math.hypot(origin.x - nearestX, origin.y - nearestY);
    const isStructural = element.matches(
      ".app-shell, .app-shell > aside, .app-shell > main, .app-shell aside, .app-shell main > *",
    );
    const isControl = element.matches("button, input, textarea, select, [role='dialog']");
    const area = rect.width * rect.height;
    nodes.push({
      element,
      delay: Math.min(560, Math.round((distance / Math.max(radius, 1)) * 640)),
      priority: isStructural ? 0 : isControl ? 1 : area > 24_000 ? 2 : 3,
    });
  });

  return nodes
    .sort((a, b) => a.priority - b.priority || a.delay - b.delay)
    .slice(0, LINKED_NODE_LIMIT);
}

function runThemeTransition(
  commit: () => void,
  nextTheme: ResolvedTheme,
  origin: ThemeTransitionOrigin,
  radius: number,
  root: HTMLElement,
) {
  const oldTheme = snapshot.resolvedTheme;
  const nodes = collectThemeNodes(origin, radius);
  nodes.forEach(({ element, delay }) => {
    element.style.setProperty("--theme-link-delay", `${delay}ms`);
    element.classList.add("theme-linked-node");
  });
  void root.offsetWidth;
  commit();
  window.dispatchEvent(new CustomEvent<ThemeTransitionDetail>("loreweft-theme-transition-start", {
    detail: {
      theme: nextTheme,
      fromTheme: oldTheme,
      origin,
      radius,
      duration: THEME_TRANSITION_DURATION,
    },
  }));

  window.setTimeout(() => {
    nodes.forEach(({ element }) => {
      element.classList.remove("theme-linked-node");
      element.style.removeProperty("--theme-link-delay");
    });
    finishThemeTransition(root);
  }, 1060);
}

function publishWithTransition(
  preference: ThemePreference,
  origin?: ThemeTransitionOrigin,
) {
  const commit = () => {
    persistPreference(preference);
    publish(preference);
  };
  const nextResolvedTheme = resolveTheme(preference);
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (themeTransitionActive) return;

  if (
    !origin
    || nextResolvedTheme === snapshot.resolvedTheme
    || reducedMotion
  ) {
    commit();
    return;
  }

  const root = document.documentElement;
  const radius = Math.hypot(
    Math.max(origin.x, window.innerWidth - origin.x),
    Math.max(origin.y, window.innerHeight - origin.y),
  );
  themeTransitionActive = true;
  root.dataset.themeTransition = "active";
  runThemeTransition(commit, nextResolvedTheme, origin, radius, root);
}

export function getThemeTransitionOrigin(element: HTMLElement): ThemeTransitionOrigin {
  const bounds = element.getBoundingClientRect();
  return {
    x: bounds.left + bounds.width / 2,
    y: bounds.top + bounds.height / 2,
  };
}

export function initializeTheme() {
  if (initialized || typeof window === "undefined") return;
  initialized = true;
  mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
  mediaQuery.addEventListener("change", () => {
    if (snapshot.preference === "system") publish("system");
  });
  publish(readPreference());
}

function subscribe(listener: () => void) {
  initializeTheme();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return snapshot;
}

export function useTheme() {
  const current = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const setTheme = useCallback((
    preference: ThemePreference,
    origin?: ThemeTransitionOrigin,
  ) => {
    initializeTheme();
    publishWithTransition(preference, origin);
  }, []);

  const toggleTheme = useCallback((origin?: ThemeTransitionOrigin) => {
    setTheme(snapshot.resolvedTheme === "night" ? "light" : "night", origin);
  }, [setTheme]);

  return {
    theme: current.preference,
    resolvedTheme: current.resolvedTheme,
    setTheme,
    toggleTheme,
  };
}
