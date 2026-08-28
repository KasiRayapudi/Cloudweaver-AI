/**
 * Command palette.
 *
 * One searchable surface over commands, templates and past designs, so the
 * keyboard is a complete route through the application rather than a shortcut
 * for a few actions. Opened with Ctrl/Cmd+K.
 */

import { store } from "./store.js";
import { TEMPLATES } from "./templates.js";
import { clear, el, icon, trapFocus } from "./ui.js";

export function createPalette({ onRunPrompt, commands }) {
  const overlay = document.getElementById("palette-overlay");
  const input = document.getElementById("palette-input");
  const list = document.getElementById("palette-list");

  let items = [];
  let cursor = 0;
  let releaseTrap = null;
  let lastFocused = null;

  function entries(query) {
    const needle = query.trim().toLowerCase();
    const matches = (text) => text.toLowerCase().includes(needle);

    const groups = [];

    const matchedCommands = commands.filter((c) => !needle || matches(c.title));
    if (matchedCommands.length) groups.push({ label: "Commands", items: matchedCommands });

    const matchedTemplates = TEMPLATES.filter(
      (t) => !needle || matches(t.title) || matches(t.description),
    ).map((template) => ({
      title: template.title,
      hint: "Template",
      iconName: template.icon,
      run: () => onRunPrompt(template.prompt, { generate: false }),
    }));
    if (matchedTemplates.length) groups.push({ label: "Templates", items: matchedTemplates });

    const matchedHistory = store.state.history
      .filter((entry) => !needle || matches(entry.prompt))
      .slice(0, 6)
      .map((entry) => ({
        title: entry.prompt,
        hint: `${entry.resources} resources`,
        iconName: "clock",
        run: () => onRunPrompt(entry.prompt, { generate: true }),
      }));
    if (matchedHistory.length) groups.push({ label: "Recent", items: matchedHistory });

    return groups;
  }

  function render(query = "") {
    clear(list);
    items = [];

    const groups = entries(query);
    if (!groups.length) {
      list.append(
        el("li", { class: "state", style: { padding: "var(--space-8)" } }, [
          el("div", { class: "state__message", text: `No matches for “${query}”.` }),
        ]),
      );
      return;
    }

    for (const group of groups) {
      list.append(el("li", { class: "palette__group", text: group.label, role: "presentation" }));
      for (const entry of group.items) {
        const index = items.length;
        const button = el("button", {
          class: "palette__item",
          type: "button",
          role: "option",
          "aria-selected": String(index === cursor),
          onClick: () => choose(index),
          onMousemove: () => setCursor(index),
        }, [
          icon(entry.iconName || "sparkles"),
          el("span", { class: "palette__item-text", text: entry.title }),
          entry.hint && el("span", { class: "badge badge--neutral", text: entry.hint }),
        ]);
        items.push({ ...entry, node: button });
        list.append(el("li", { role: "presentation" }, [button]));
      }
    }
    setCursor(Math.min(cursor, items.length - 1));
  }

  function setCursor(next) {
    if (!items.length) return;
    cursor = (next + items.length) % items.length;
    items.forEach((entry, index) => {
      entry.node.setAttribute("aria-selected", String(index === cursor));
    });
    items[cursor].node.scrollIntoView({ block: "nearest" });
  }

  function choose(index) {
    const entry = items[index];
    if (!entry) return;
    close();
    entry.run();
  }

  function open() {
    lastFocused = document.activeElement;
    overlay.hidden = false;
    input.value = "";
    cursor = 0;
    render("");
    input.focus();
    releaseTrap = trapFocus(overlay, close);
  }

  function close() {
    overlay.hidden = true;
    releaseTrap?.();
    releaseTrap = null;
    lastFocused?.focus?.();
  }

  input.addEventListener("input", () => {
    cursor = 0;
    render(input.value);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { event.preventDefault(); setCursor(cursor + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setCursor(cursor - 1); }
    else if (event.key === "Enter") { event.preventDefault(); choose(cursor); }
  });

  // Clicking the backdrop, but not the panel, dismisses.
  overlay.addEventListener("mousedown", (event) => {
    if (event.target === overlay) close();
  });

  return { open, close, get isOpen() { return !overlay.hidden; } };
}
