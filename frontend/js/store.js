/**
 * Application state and persistence.
 *
 * A tiny observable store rather than a framework: the application has one
 * result at a time plus a list of past ones, and that does not justify a
 * dependency. Subscribers re-render on change; nothing else mutates state.
 *
 * History lives in localStorage so a refresh does not lose the user's work,
 * which was the single most costly gap in the previous interface. Every
 * access is guarded: private windows and blocked site data throw on read.
 */

const STORAGE_KEY = "cloudweaver.v1";
const MAX_HISTORY = 40;

function readStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;   // private window, cleared data, or storage disabled
  }
}

function writeStorage(value) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    /* Persistence is a convenience; losing it must never break the app. */
  }
}

const persisted = readStorage() || {};

const state = {
  /** "idle" | "generating" | "ready" | "error" */
  status: "idle",
  prompt: "",
  result: null,
  error: null,
  extractor: persisted.extractor === "llm" ? "llm" : "rule",
  // Dark by default rather than following the operating system. This is a
  // console for reading diagrams and code, where a dark ground is the
  // convention, and a demo should not depend on the presenter's OS setting.
  // An explicit choice still wins and is remembered.
  theme: persisted.theme || "dark",
  sidebar: persisted.sidebar === "collapsed" ? "collapsed" : "expanded",
  activeTab: "overview",
  selectedResource: null,
  history: Array.isArray(persisted.history) ? persisted.history : [],
  favourites: Array.isArray(persisted.favourites) ? persisted.favourites : [],
  examples: [],
  health: null,
};

const listeners = new Set();

function notify(changed) {
  for (const listener of listeners) listener(state, changed);
}

function persist() {
  writeStorage({
    extractor: state.extractor,
    theme: state.theme,
    sidebar: state.sidebar,
    history: state.history,
    favourites: state.favourites,
  });
}

export const store = {
  get state() {
    return state;
  },

  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  set(changes, { persistState = false } = {}) {
    Object.assign(state, changes);
    if (persistState) persist();
    notify(Object.keys(changes));
  },

  /** Record a generation so it can be reopened without regenerating. */
  remember(prompt, result) {
    const entry = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      prompt,
      at: Date.now(),
      name: result.summary.name,
      resources: result.summary.resource_count,
      cost: result.summary.estimated_monthly_cost_usd,
      region: result.summary.region,
      environment: result.summary.environment,
      errors: result.findings.filter((f) => f.severity === "error").length,
    };
    // De-duplicate on the prompt so re-running the same thing moves it to the
    // top rather than filling the list with copies.
    const rest = state.history.filter((item) => item.prompt !== prompt);
    state.history = [entry, ...rest].slice(0, MAX_HISTORY);
    persist();
    notify(["history"]);
    return entry;
  },

  toggleFavourite(prompt) {
    const exists = state.favourites.includes(prompt);
    state.favourites = exists
      ? state.favourites.filter((item) => item !== prompt)
      : [prompt, ...state.favourites].slice(0, MAX_HISTORY);
    persist();
    notify(["favourites"]);
    return !exists;
  },

  isFavourite(prompt) {
    return state.favourites.includes(prompt);
  },

  clearHistory() {
    state.history = [];
    persist();
    notify(["history"]);
  },
};

/**
 * Apply the theme to the document.
 *
 * "system" deliberately stamps nothing, leaving prefers-color-scheme in
 * charge; stamping a resolved value would freeze the page against a later
 * change of operating system theme.
 */
export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}
