/**
 * Shared UI primitives: element construction, icons, toasts, clipboard.
 *
 * `el` exists so components build DOM with real nodes rather than innerHTML.
 * Everything on this page renders values that came from a user's prompt, and
 * a template string would make an injection possible the moment one of those
 * values reached the markup unescaped.
 */

/** Build an element. Attributes are set as properties where possible. */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);

  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;      // icons only
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "style" && typeof value === "object") {
      Object.assign(node.style, value);
    } else node.setAttribute(key, value === true ? "" : String(value));
  }

  for (const child of [children].flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/* ------------------------------------------------------------------
   Icons. Inline 16px strokes from one set, so weight stays consistent.
   ------------------------------------------------------------------ */
const PATHS = {
  sparkles: '<path d="M8 2.5 9.4 6.6 13.5 8 9.4 9.4 8 13.5 6.6 9.4 2.5 8 6.6 6.6z"/><path d="M13 2v2M12 3h2"/>',
  search: '<circle cx="7.2" cy="7.2" r="4.7"/><path d="m10.8 10.8 3 3"/>',
  home: '<path d="M2.5 7 8 2.5 13.5 7v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z"/><path d="M6.2 14V9h3.6v5"/>',
  layers: '<path d="M8 1.8 14.5 5 8 8.2 1.5 5z"/><path d="m1.5 8 6.5 3.2L14.5 8"/><path d="m1.5 11 6.5 3.2L14.5 11"/>',
  clock: '<circle cx="8" cy="8" r="6"/><path d="M8 4.6V8l2.3 1.4"/>',
  star: '<path d="m8 2 1.8 3.9 4.2.5-3.1 2.9.8 4.2L8 11.6 4.3 13.5l.8-4.2L2 6.4l4.2-.5z"/>',
  book: '<path d="M2.5 3.2h4a2 2 0 0 1 2 2v8a1.6 1.6 0 0 0-1.6-1.4H2.5z"/><path d="M13.5 3.2h-4a2 2 0 0 0-2 2v8a1.6 1.6 0 0 1 1.6-1.4h4.4z"/>',
  code: '<path d="m5.5 5.5-3 2.5 3 2.5"/><path d="m10.5 5.5 3 2.5-3 2.5"/>',
  shield: '<path d="M8 1.8 13.2 4v4c0 3.2-2.2 5.4-5.2 6.2C5 13.4 2.8 11.2 2.8 8V4z"/>',
  chart: '<path d="M2.5 13.5h11"/><path d="M4.5 13.5V9M7.5 13.5V5M10.5 13.5v-3M13.5 13.5V7"/>',
  route: '<circle cx="4" cy="4" r="1.8"/><circle cx="12" cy="12" r="1.8"/><path d="M5.8 4h3.4a2.8 2.8 0 0 1 0 5.6H6.8a2.8 2.8 0 0 0 0 5.6"/>',
  copy: '<rect x="5.5" y="5.5" width="8" height="8" rx="1.4"/><path d="M10.5 3.5h-7a1 1 0 0 0-1 1v7"/>',
  download: '<path d="M8 2.5v7.5"/><path d="m5 7.2 3 3 3-3"/><path d="M2.8 12.5h10.4"/>',
  check: '<path d="m3 8.4 3.2 3.2L13 4.8"/>',
  alert: '<path d="M8 2.8 14.5 13.2h-13z"/><path d="M8 6.6v3M8 11.4h.01"/>',
  info: '<circle cx="8" cy="8" r="6"/><path d="M8 7.4v3.4M8 5.2h.01"/>',
  sun: '<circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M14.5 8h-1.6M3.1 8H1.5M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1M12.6 12.6l-1.1-1.1M4.5 4.5 3.4 3.4"/>',
  moon: '<path d="M13.2 9.4A5.6 5.6 0 0 1 6.6 2.8a5.8 5.8 0 1 0 6.6 6.6z"/>',
  panel: '<rect x="2" y="3" width="12" height="10" rx="1.6"/><path d="M6.2 3v10"/>',
  trash: '<path d="M3 4.5h10"/><path d="M5.5 4.5V3.2h5v1.3"/><path d="M4.2 4.5 5 13.2h6l.8-8.7"/>',
  arrowUp: '<path d="M8 13V3.5"/><path d="m4.5 7 3.5-3.5L11.5 7"/>',
  x: '<path d="m4 4 8 8M12 4l-8 8"/>',
  enter: '<path d="M13 4v3.5a2 2 0 0 1-2 2H3.5"/><path d="m6 7-2.5 2.5L6 12"/>',
  bolt: '<path d="M9 1.8 3.5 9h4l-.5 5.2L12.5 7h-4z"/>',
  server: '<rect x="2" y="2.8" width="12" height="4.4" rx="1.2"/><rect x="2" y="8.8" width="12" height="4.4" rx="1.2"/><path d="M4.6 5h.01M4.6 11h.01"/>',
};

/** An inline icon. `name` must exist in PATHS. */
export function icon(name, size = 16) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.5");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = PATHS[name] || PATHS.info;   // from a fixed table, never user input
  return svg;
}

/* ------------------------------------------------------------------
   Toasts
   ------------------------------------------------------------------ */
let toastRegion = null;

function ensureToastRegion() {
  if (toastRegion) return toastRegion;
  toastRegion = el("div", {
    class: "toast-region",
    role: "region",
    "aria-live": "polite",
    "aria-label": "Notifications",
  });
  document.body.append(toastRegion);
  return toastRegion;
}

const TOAST_ICONS = { success: "check", error: "alert", warning: "alert", info: "info" };

/** Show a transient message. Errors linger; confirmations do not. */
export function toast(title, { message = "", variant = "info", duration } = {}) {
  const region = ensureToastRegion();
  const life = duration ?? (variant === "error" ? 7000 : 3600);

  const node = el("div", { class: `toast toast--${variant}`, role: "status" }, [
    icon(TOAST_ICONS[variant] || "info"),
    el("div", { class: "toast__body" }, [
      el("div", { class: "toast__title", text: title }),
      message && el("div", { class: "toast__message", text: message }),
    ]),
    el("button", {
      class: "btn btn--ghost btn--icon btn--sm",
      "aria-label": "Dismiss notification",
      onClick: () => dismiss(),
    }, [icon("x", 14)]),
  ]);

  function dismiss() {
    node.classList.add("is-leaving");
    node.addEventListener("animationend", () => node.remove(), { once: true });
  }

  region.append(node);
  const timer = setTimeout(dismiss, life);
  node.addEventListener("mouseenter", () => clearTimeout(timer));
  return dismiss;
}

/* ------------------------------------------------------------------
   Clipboard and downloads
   ------------------------------------------------------------------ */
export async function copyText(text, label = "Copied") {
  try {
    await navigator.clipboard.writeText(text);
    toast(label, { variant: "success" });
    return true;
  } catch {
    toast("Could not copy", {
      message: "Your browser blocked clipboard access. Select the text and copy manually.",
      variant: "error",
    });
    return false;
  }
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function downloadText(text, filename, type = "text/plain") {
  downloadBlob(new Blob([text], { type }), filename);
}

/* ------------------------------------------------------------------
   Formatting
   ------------------------------------------------------------------ */
export function formatCurrency(value) {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: value >= 100 ? 0 : 2,
  }).format(value || 0);
}

export function formatRelative(timestamp) {
  const seconds = Math.round((timestamp - Date.now()) / 1000);
  const units = [
    ["day", 86400], ["hour", 3600], ["minute", 60],
  ];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return "just now";
}

/** Trap Tab within a container while a dialog is open. */
export function trapFocus(container, onEscape) {
  const selector =
    'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])';

  function onKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      onEscape?.();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...container.querySelectorAll(selector)].filter(
      (node) => node.offsetParent !== null,
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  container.addEventListener("keydown", onKeydown);
  return () => container.removeEventListener("keydown", onKeydown);
}
