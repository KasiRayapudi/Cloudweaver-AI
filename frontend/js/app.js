/**
 * Application bootstrap.
 *
 * Owns the shell: composer, sidebar, theme, palette and the generation
 * lifecycle. Rendering of the result itself lives in results.js so this file
 * stays about orchestration rather than markup.
 */

import { ApiError, api } from "./api.js";
import { createPalette } from "./palette.js";
import { renderPipeline, renderResult } from "./results.js";
import { applyTheme, store } from "./store.js";
import {
  clear, copyText, downloadBlob, el, formatRelative, icon, toast,
} from "./ui.js";
import { SUGGESTIONS, TEMPLATES } from "./templates.js";

const MAX_PROMPT = 4000;
const $ = (id) => document.getElementById(id);

const dom = {
  shell: $("shell"),
  prompt: $("prompt"),
  generate: $("generate"),
  counter: $("counter"),
  hero: $("hero"),
  templates: $("templates"),
  templateFeatured: $("template-featured"),
  templateRows: $("template-rows"),
  suggestions: $("suggestions"),
  results: $("results"),
  resultsBody: $("results-body"),
  historyList: $("history-list"),
  favouritesList: $("favourites-list"),
  favouritesSection: $("favourites-section"),
  topbarName: $("topbar-name"),
  topbarBadge: $("topbar-badge"),
  useLlm: $("use-llm"),
  navResourceCount: $("nav-resource-count"),
};

let inFlight = null;

/* ------------------------------------------------------------------
   Icons declared in the HTML
   ------------------------------------------------------------------ */
function hydrateIcons(root = document) {
  for (const slot of root.querySelectorAll("[data-icon]")) {
    slot.replaceWith(icon(slot.dataset.icon));
  }
}

/* ------------------------------------------------------------------
   Composer
   ------------------------------------------------------------------ */
function autoGrow() {
  const field = dom.prompt;
  field.style.height = "auto";
  field.style.height = `${Math.min(field.scrollHeight, 340)}px`;
}

function updateCounter() {
  const length = dom.prompt.value.length;
  dom.counter.textContent = `${length.toLocaleString()} / ${MAX_PROMPT.toLocaleString()}`;
  dom.counter.classList.toggle("is-warning", length > MAX_PROMPT * 0.9);
  dom.generate.disabled = length < 3 || store.state.status === "generating";
}

function setPrompt(text, { focus = true } = {}) {
  dom.prompt.value = text;
  autoGrow();
  updateCounter();
  if (focus) {
    dom.prompt.focus();
    dom.prompt.setSelectionRange(text.length, text.length);
  }
}

/* ------------------------------------------------------------------
   Rotating placeholder
   ------------------------------------------------------------------
   Shows the shape of a good prompt without the user having to read a
   manual. Rotation stops the moment they focus or type: a placeholder
   changing under someone who is composing is a distraction, not a hint.
   ------------------------------------------------------------------ */
const PLACEHOLDERS = [
  "A production web app in eu-west-1: an auto scaling group behind an "
    + "application load balancer with HTTPS, a Multi-AZ PostgreSQL database, "
    + "and an S3 bucket for uploads.",
  "API Gateway in front of a Python Lambda that reads a DynamoDB table, "
    + "with an SQS queue for background jobs.",
  "An EKS cluster with 4 t3.medium nodes in private subnets, a bastion host "
    + "and an S3 bucket for artifacts.",
  "An EC2 instance called web-01 in my existing VPC vpc-0abc123def456, "
    + "allowing SSH and HTTPS.",
  "A static site in S3 behind CloudFront with a Route 53 domain and a WAF.",
  "A network load balancer with TLS termination on port 443 in front of "
    + "two web servers.",
];

let placeholderTimer = null;

function startPlaceholderRotation() {
  let index = 0;
  const field = dom.prompt;
  field.placeholder = PLACEHOLDERS[0];

  function stop() {
    clearInterval(placeholderTimer);
    placeholderTimer = null;
  }

  placeholderTimer = setInterval(() => {
    if (field.value || document.activeElement === field) return;
    index = (index + 1) % PLACEHOLDERS.length;
    field.classList.add("is-fading");
    setTimeout(() => {
      field.placeholder = PLACEHOLDERS[index];
      field.classList.remove("is-fading");
    }, 180);
  }, 5200);

  field.addEventListener("focus", stop, { once: true });
  field.addEventListener("input", stop, { once: true });
}

/* ------------------------------------------------------------------
   Generation
   ------------------------------------------------------------------ */
async function generate() {
  const prompt = dom.prompt.value.trim();
  if (prompt.length < 3) {
    toast("Describe what you need first", {
      message: "Even a short phrase works — “two web servers and a database”.",
      variant: "warning",
    });
    dom.prompt.focus();
    return;
  }

  inFlight?.abort();
  inFlight = new AbortController();

  store.set({ status: "generating", prompt, error: null });
  setBusy(true);
  showResultsRegion();
  const progress = renderPipeline(dom.resultsBody);

  try {
    const result = await api.generate(prompt, {
      extractor: store.state.extractor,
      signal: inFlight.signal,
    });

    progress.finish(result.summary.duration_ms);

    // The architecture is what the user came to see, so generation lands on
    // it rather than on a summary they have to click past.
    store.set({
      status: "ready", result, selectedResource: null, activeTab: "diagram",
    });
    store.remember(prompt, result);
    paintResult(result);

    const errors = result.findings.filter((f) => f.severity === "error").length;
    toast(`Generated ${result.summary.resource_count} resources`, {
      message: errors
        ? `${errors} finding${errors === 1 ? "" : "s"} need attention before deploying.`
        : `${result.summary.file_count} Terraform files in ${Math.round(result.summary.duration_ms)} ms.`,
      variant: errors ? "warning" : "success",
    });
  } catch (error) {
    if (error.name === "ApiError" && error.message.includes("cancelled")) {
      progress.stop();
      return;
    }
    progress.fail(error.message);
    store.set({ status: "error", error });
    renderError(error);
  } finally {
    setBusy(false);
    inFlight = null;
  }
}

function setBusy(busy) {
  dom.generate.disabled = busy;
  dom.generate.setAttribute("aria-busy", String(busy));
  clear(dom.generate).append(
    busy ? el("span", { class: "btn__spinner" }) : icon("sparkles"),
    el("span", { text: busy ? "Generating…" : "Generate" }),
  );
  // A cancel control only exists while there is something to cancel.
  const existing = $("cancel-generation");
  if (busy && !existing) {
    dom.generate.after(
      el("button", {
        class: "btn btn--ghost btn--sm",
        id: "cancel-generation",
        type: "button",
        onClick: () => {
          inFlight?.abort();
          toast("Generation cancelled", { variant: "info", duration: 2000 });
        },
      }, [el("span", { text: "Cancel" })]),
    );
  } else if (!busy) {
    existing?.remove();
  }
  if (!busy) updateCounter();
}

function showResultsRegion() {
  dom.results.hidden = false;
  dom.hero.hidden = true;
  dom.templates.hidden = true;
  dom.results.scrollIntoView({ behavior: "smooth", block: "start" });
}

function paintResult(result) {
  renderResult(dom.resultsBody, result, {
    onCopy: copyText,
    onDownload: downloadProject,
  });
  dom.topbarName.textContent = result.summary.name;
  dom.topbarBadge.hidden = false;
  dom.topbarBadge.textContent = `${result.summary.region} · ${result.summary.environment}`;
  dom.navResourceCount.textContent = String(result.summary.resource_count);
  const navResult = document.querySelector('[data-nav="result"]');
  navResult.disabled = false;
}

function renderError(error) {
  const isProvider = /not supported/i.test(error.message);
  clear(dom.resultsBody).append(
    el("div", { class: "panel" }, [
      el("div", { class: "state state--error" }, [
        el("div", { class: "state__icon" }, [icon("alert", 20)]),
        el("div", { class: "state__title", text: isProvider ? "Unsupported cloud provider" : "Could not generate" }),
        el("p", { class: "state__message", text: error.message }),
        el("div", { style: { display: "flex", gap: "var(--space-2)" } }, [
          error.retryable &&
            el("button", { class: "btn btn--primary btn--sm", onClick: generate }, [
              icon("bolt"), el("span", { text: "Try again" }),
            ]),
          el("button", {
            class: "btn btn--secondary btn--sm",
            onClick: () => { dom.prompt.focus(); },
          }, [el("span", { text: "Edit the prompt" })]),
        ]),
      ]),
    ]),
  );
}

async function downloadProject() {
  try {
    const blob = await api.download(store.state.prompt, {
      extractor: store.state.extractor,
    });
    downloadBlob(blob, `${store.state.result.summary.name}.zip`);
    toast("Project downloaded", { variant: "success" });
  } catch (error) {
    toast("Download failed", { message: error.message, variant: "error" });
  }
}

/* ------------------------------------------------------------------
   Sidebar lists
   ------------------------------------------------------------------ */
function renderHistory() {
  const { history } = store.state;
  clear(dom.historyList);

  if (!history.length) {
    dom.historyList.append(
      el("p", {
        class: "sidebar__label",
        style: { textTransform: "none", letterSpacing: "0", color: "var(--fg-faint)" },
        text: "Nothing yet",
      }),
    );
    return;
  }

  for (const entry of history.slice(0, 8)) {
    dom.historyList.append(
      el("button", {
        class: "nav-item",
        title: entry.prompt,
        onClick: () => setPrompt(entry.prompt),
      }, [
        icon(entry.errors ? "alert" : "clock"),
        el("span", { class: "nav-item__text", text: entry.name || entry.prompt }),
        el("span", { class: "nav-item__meta", text: formatRelative(entry.at) }),
      ]),
    );
  }
}

function renderFavourites() {
  const { favourites } = store.state;
  dom.favouritesSection.hidden = favourites.length === 0;
  clear(dom.favouritesList);
  for (const prompt of favourites.slice(0, 8)) {
    dom.favouritesList.append(
      el("button", {
        class: "nav-item",
        title: prompt,
        onClick: () => setPrompt(prompt),
      }, [
        icon("star"),
        el("span", { class: "nav-item__text", text: prompt }),
      ]),
    );
  }
}

/* ------------------------------------------------------------------
   Gallery
   ------------------------------------------------------------------ */
function loadTemplate(template) {
  setPrompt(template.prompt);
  toast("Template loaded", {
    message: "Edit it, then press Generate.",
    variant: "info",
    duration: 2400,
  });
}

function renderTemplates() {
  // Two tiers: eight identical cards gave no card the weight of a
  // recommendation, and cost 1.4 screens of scroll to say very little.
  const [first, second, ...rest] = TEMPLATES;

  clear(dom.templateFeatured);
  for (const template of [first, second]) {
    dom.templateFeatured.append(
      el("button", {
        class: "card card--interactive template-hero",
        type: "button",
        onClick: () => loadTemplate(template),
      }, [
        el("span", { class: "template-hero__icon" }, [icon(template.icon, 18)]),
        el("span", { class: "template-hero__title" }, [
          el("span", { text: template.title }),
          el("span", { class: "badge badge--brand", text: "Popular" }),
        ]),
        el("span", { class: "template-hero__desc", text: template.description }),
        el("span", { class: "template-hero__meta" },
          template.tags.map((tag) => el("span", { class: "badge badge--neutral", text: tag })),
        ),
      ]),
    );
  }

  clear(dom.templateRows);
  for (const template of rest) {
    dom.templateRows.append(
      el("li", {}, [
        el("button", {
          class: "template-row",
          type: "button",
          onClick: () => loadTemplate(template),
        }, [
          el("span", { class: "template-row__icon" }, [icon(template.icon, 15)]),
          el("span", { class: "template-row__title", text: template.title }),
          el("span", { class: "template-row__desc", text: template.description }),
          el("span", { class: "template-row__go" }, [icon("enter", 14)]),
        ]),
      ]),
    );
  }
}

function renderSuggestions() {
  clear(dom.suggestions);
  for (const suggestion of SUGGESTIONS) {
    dom.suggestions.append(
      el("button", {
        class: "chip",
        type: "button",
        onClick: () => setPrompt(suggestion),
      }, [icon("sparkles", 13), el("span", { text: suggestion })]),
    );
  }
}

/* ------------------------------------------------------------------
   Theme and layout
   ------------------------------------------------------------------ */
const THEME_ORDER = ["system", "light", "dark"];

function cycleTheme() {
  const next = THEME_ORDER[(THEME_ORDER.indexOf(store.state.theme) + 1) % THEME_ORDER.length];
  store.set({ theme: next }, { persistState: true });
  applyTheme(next);
  updateThemeButton();
  toast(`Theme: ${next}`, { variant: "info", duration: 1800 });
}

function updateThemeButton() {
  const button = $("theme-toggle");
  const theme = store.state.theme;
  clear(button).append(
    icon(theme === "dark" ? "moon" : "sun"),
    el("span", { text: theme === "system" ? "Theme" : theme[0].toUpperCase() + theme.slice(1) }),
  );
  button.setAttribute("aria-label", `Theme: ${theme}. Click to change.`);
}

function toggleSidebar() {
  const next = store.state.sidebar === "collapsed" ? "expanded" : "collapsed";
  store.set({ sidebar: next }, { persistState: true });
  dom.shell.dataset.sidebar = next;
  $("sidebar-toggle").setAttribute(
    "aria-label", next === "collapsed" ? "Expand sidebar" : "Collapse sidebar",
  );
}

/* ------------------------------------------------------------------
   Top bar menus
   ------------------------------------------------------------------ */

/**
 * One open menu at a time, closed by Escape, an outside click, or opening
 * the other. Written once here rather than per menu so the two cannot get
 * out of step.
 */
function wireMenus() {
  const menus = [
    { trigger: $("settings-trigger"), panel: $("settings-menu") },
    { trigger: $("status-trigger"), panel: $("status-menu") },
  ];

  function closeAll(except) {
    for (const entry of menus) {
      if (entry === except) continue;
      entry.panel.hidden = true;
      entry.trigger.setAttribute("aria-expanded", "false");
    }
  }

  for (const entry of menus) {
    entry.trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const opening = entry.panel.hidden;
      closeAll(entry);
      entry.panel.hidden = !opening;
      entry.trigger.setAttribute("aria-expanded", String(opening));
      if (opening) {
        if (entry.panel === $("status-menu")) paintStatus();
        entry.panel.querySelector(".menu__item")?.focus();
      }
    });
    entry.panel.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      entry.panel.hidden = true;
      entry.trigger.setAttribute("aria-expanded", "false");
      entry.trigger.focus();
    });
  }

  document.addEventListener("click", () => closeAll(null));

  for (const button of document.querySelectorAll("[data-theme-set]")) {
    button.addEventListener("click", () => {
      const theme = button.dataset.themeSet;
      store.set({ theme }, { persistState: true });
      applyTheme(theme);
      updateThemeButton();
      closeAll(null);
      toast(`Theme set to ${theme}`, { variant: "success", duration: 1800 });
    });
  }

  $("menu-clear-history").addEventListener("click", () => {
    store.clearHistory();
    closeAll(null);
    toast("History cleared", { variant: "info", duration: 2000 });
  });
}

/** Live figures, so the menu reports the session rather than a fixed blurb. */
function paintStatus() {
  const list = $("status-list");
  const health = store.state.health;
  const result = store.state.result;

  const rows = [
    ["Backend", health ? `Online · v${health.version}` : "Unreachable"],
    ["Extractor", health ? health.extractor : "unknown"],
    ["LLM extraction", health?.llm_available ? "Available" : "Not configured"],
    ["Designs this session", String(store.state.history.length)],
  ];
  if (result) {
    rows.push(
      ["Last generation", `${Math.round(result.summary.duration_ms)} ms`],
      ["Resources", String(result.summary.resource_count)],
    );
  }

  clear(list);
  for (const [label, value] of rows) {
    list.append(
      el("dt", { text: label }),
      el("dd", { text: value }),
    );
  }
}

/* ------------------------------------------------------------------
   Wiring
   ------------------------------------------------------------------ */
function bind(palette) {
  dom.prompt.addEventListener("input", () => { autoGrow(); updateCounter(); });
  dom.prompt.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      generate();
    }
  });

  dom.generate.addEventListener("click", generate);

  $("clear-prompt").addEventListener("click", () => {
    setPrompt("");
    dom.results.hidden = true;
    dom.hero.hidden = false;
    dom.templates.hidden = false;
  });

  $("favourite-prompt").addEventListener("click", () => {
    const prompt = dom.prompt.value.trim();
    if (!prompt) return;
    const added = store.toggleFavourite(prompt);
    toast(added ? "Saved to favourites" : "Removed from favourites", {
      variant: "success", duration: 2000,
    });
  });

  $("clear-history").addEventListener("click", () => {
    store.clearHistory();
    toast("History cleared", { variant: "info", duration: 2000 });
  });

  $("theme-toggle").addEventListener("click", cycleTheme);
  wireMenus();
  $("sidebar-toggle").addEventListener("click", toggleSidebar);
  $("palette-trigger").addEventListener("click", palette.open);
  $("browse-all-templates")?.addEventListener("click", palette.open);

  dom.useLlm.addEventListener("change", () => {
    store.set({ extractor: dom.useLlm.checked ? "llm" : "rule" }, { persistState: true });
  });

  for (const button of document.querySelectorAll("[data-nav]")) {
    button.addEventListener("click", () => {
      for (const other of document.querySelectorAll("[data-nav]")) {
        other.setAttribute("aria-current", String(other === button));
      }
      const target = button.dataset.nav;
      if (target === "compose") {
        dom.hero.hidden = Boolean(store.state.result);
        dom.templates.hidden = false;
        dom.prompt.focus();
        dom.prompt.scrollIntoView({ behavior: "smooth", block: "center" });
      } else if (target === "templates") {
        dom.templates.hidden = false;
        dom.templates.scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (target === "result" && store.state.result) {
        dom.results.hidden = false;
        dom.results.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }

  // Global shortcuts.
  window.addEventListener("keydown", (event) => {
    const typing = /^(INPUT|TEXTAREA)$/.test(event.target.tagName);

    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      palette.isOpen ? palette.close() : palette.open();
      return;
    }
    if (event.key === "/" && !typing) {
      event.preventDefault();
      dom.prompt.focus();
    }
  });

  store.subscribe((_, changed) => {
    if (changed.includes("history")) renderHistory();
    if (changed.includes("favourites")) renderFavourites();
  });
}

/* ------------------------------------------------------------------
   Start
   ------------------------------------------------------------------ */
async function start() {
  hydrateIcons();
  applyTheme(store.state.theme);
  updateThemeButton();
  dom.shell.dataset.sidebar = store.state.sidebar;
  dom.useLlm.checked = store.state.extractor === "llm";

  renderTemplates();
  renderSuggestions();
  startPlaceholderRotation();
  renderHistory();
  renderFavourites();
  autoGrow();
  updateCounter();

  const palette = createPalette({
    onRunPrompt: (prompt, { generate: run }) => {
      setPrompt(prompt);
      if (run) generate();
    },
    commands: [
      { title: "Generate infrastructure", iconName: "sparkles", run: generate },
      { title: "Clear the prompt", iconName: "x", run: () => setPrompt("") },
      { title: "Toggle theme", iconName: "sun", run: cycleTheme },
      { title: "Collapse or expand the sidebar", iconName: "panel", run: toggleSidebar },
      {
        title: "Download the Terraform project",
        iconName: "download",
        run: () => (store.state.result ? downloadProject() : toast("Generate something first", { variant: "warning" })),
      },
      { title: "Clear history", iconName: "trash", run: () => store.clearHistory() },
    ],
  });

  bind(palette);

  // Health is advisory: the app is usable whatever it says.
  try {
    const health = await api.health();
    store.set({ health });
    $("status-dot").classList.add("is-online");
    if (!health.llm_available) {
      dom.useLlm.disabled = true;
      dom.useLlm.closest(".switch").dataset.tooltip =
        "Set ANTHROPIC_API_KEY on the server to enable LLM extraction";
      if (store.state.extractor === "llm") {
        store.set({ extractor: "rule" }, { persistState: true });
        dom.useLlm.checked = false;
      }
    }
  } catch {
    $("status-dot").classList.add("is-offline");
    toast("Backend unreachable", {
      message: "Start the server with: python -m uvicorn app.main:app --app-dir backend",
      variant: "error",
    });
  }
}

start();
