/**
 * Interactive architecture viewer.
 *
 * There is no diagram library here: the SVG arrives fully laid out from the
 * backend's own layout engine, so "fit to view" is implemented against the
 * document's `viewBox` rather than by calling a library's `fitView()`.
 *
 * That turns out to be an advantage. The viewBox is the exact bounding box of
 * the generated content, so the fit is precise by construction and no bounds
 * calculation can drift from what was actually drawn.
 */

import { createFilters, renderFilterBar } from "./diagram-filters.js";
import { api } from "./api.js";
import { clear, downloadText, el, icon, toast } from "./ui.js";

const MIN_ZOOM = 0.15;
const MAX_ZOOM = 4;
const FIT_PADDING = 0.94;   // leave a margin so nothing touches the edge

export function createDiagramViewer(result, { onSelectResource } = {}) {
  const stage = el("div", {
    class: "diagram-stage",
    tabindex: "0",
    role: "application",
    "aria-label":
      "Architecture diagram. Drag to pan, Ctrl and scroll to zoom, " +
      "arrow keys to move, plus and minus to zoom, 0 to fit.",
  });
  stage.innerHTML = result.diagram.svg;

  const svg = stage.querySelector("svg");
  const viewport = el("div", { class: "diagram-viewport" });

  // The SVG is moved inside a transform wrapper so panning and zooming never
  // touch the SVG's own attributes; the document stays exactly as generated
  // and remains valid to export at any moment.
  if (svg) {
    // The viewport is sized to the drawing's intrinsic box and the transform
    // scales it. Stripping width/height without sizing the wrapper collapses
    // the SVG to 0x0: a viewBox alone gives a replaced element no height.
    const box = svg.viewBox?.baseVal;
    const width = box?.width || Number(svg.getAttribute("width")) || 1200;
    const height = box?.height || Number(svg.getAttribute("height")) || 800;
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.style.width = `${width}px`;
    svg.style.height = `${height}px`;
    viewport.style.width = `${width}px`;
    viewport.style.height = `${height}px`;
    viewport.append(svg);
    stage.append(viewport);
  }

  const mermaidView = el("pre", { class: "code code--boxed", hidden: true }, [
    el("code", { text: result.diagram.mermaid }),
  ]);

  const state = { zoom: 1, x: 0, y: 0, animate: true };
  let selected = null;
  // Set by any manual zoom or pan. A resize refits an untouched view, but
  // preserves a view the user has deliberately positioned -- silently
  // throwing away someone's chosen zoom because they widened the window is
  // the more annoying of the two failures.
  let userAdjusted = false;

  /** Intrinsic size of the drawing, from the viewBox the backend emitted. */
  function contentSize() {
    const box = svg?.viewBox?.baseVal;
    if (box && box.width) return { width: box.width, height: box.height };
    return { width: 1200, height: 800 };
  }

  function apply() {
    if (!svg) return;
    viewport.style.transition = state.animate
      ? `transform var(--duration-base) var(--ease-out)`
      : "none";
    viewport.style.transform =
      `translate(${state.x}px, ${state.y}px) scale(${state.zoom})`;
    zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
    updateMinimap();
  }

  /**
   * Scale the drawing so all of it is visible, then centre it.
   *
   * Run after first render, on every resize, and whenever the panel becomes
   * visible: a tab that was hidden has zero measurable size, so fitting while
   * hidden would compute a scale from nothing.
   */
  function fit({ animate = true } = {}) {
    const area = stage.getBoundingClientRect();
    if (area.width < 40 || area.height < 40) return false;   // not visible yet

    const { width, height } = contentSize();
    const scale = Math.min(area.width / width, area.height / height) * FIT_PADDING;

    state.animate = animate;
    state.zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
    state.x = (area.width - width * state.zoom) / 2;
    state.y = (area.height - height * state.zoom) / 2;
    apply();
    state.animate = true;
    userAdjusted = false;
    return true;
  }

  function centre({ animate = true } = {}) {
    const area = stage.getBoundingClientRect();
    const { width, height } = contentSize();
    state.animate = animate;
    state.x = (area.width - width * state.zoom) / 2;
    state.y = (area.height - height * state.zoom) / 2;
    apply();
  }

  /** Zoom about a point so the content under the cursor stays under it. */
  function zoomTo(next, anchor) {
    const area = stage.getBoundingClientRect();
    const target = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
    const px = anchor ? anchor.x - area.left : area.width / 2;
    const py = anchor ? anchor.y - area.top : area.height / 2;

    state.x = px - ((px - state.x) / state.zoom) * target;
    state.y = py - ((py - state.y) / state.zoom) * target;
    state.zoom = target;
    userAdjusted = true;
    apply();
  }

  /* ---------------- interaction ---------------- */

  let dragging = false;
  let start = { x: 0, y: 0 };

  stage.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    dragging = true;
    state.animate = false;                       // dragging must track exactly
    start = { x: event.clientX - state.x, y: event.clientY - state.y };
    stage.setPointerCapture(event.pointerId);
    stage.classList.add("is-grabbing");
  });

  stage.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    state.x = event.clientX - start.x;
    state.y = event.clientY - start.y;
    userAdjusted = true;
    apply();
  });

  function endDrag(event) {
    if (!dragging) return;
    dragging = false;
    state.animate = true;
    stage.releasePointerCapture?.(event.pointerId);
    stage.classList.remove("is-grabbing");
  }
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);

  stage.addEventListener("wheel", (event) => {
    // Plain scroll belongs to the page; only a modifier zooms, which is the
    // convention every map and canvas tool uses.
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    state.animate = false;
    zoomTo(state.zoom * (1 - event.deltaY * 0.0016), { x: event.clientX, y: event.clientY });
    state.animate = true;
  }, { passive: false });

  stage.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? 60 : 24;
    const moves = {
      ArrowLeft: [step, 0], ArrowRight: [-step, 0],
      ArrowUp: [0, step], ArrowDown: [0, -step],
    };
    if (moves[event.key]) {
      event.preventDefault();
      state.x += moves[event.key][0];
      state.y += moves[event.key][1];
      apply();
    } else if (event.key === "+" || event.key === "=") {
      event.preventDefault(); zoomTo(state.zoom * 1.2);
    } else if (event.key === "-" || event.key === "_") {
      event.preventDefault(); zoomTo(state.zoom / 1.2);
    } else if (event.key === "0") {
      event.preventDefault(); fit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      filters.reset();
      filterBar.paintFocus();
    }
  });

  /* ---------------- node selection ---------------- */

  function selectNode(group) {
    const id = group?.dataset.resourceId;
    if (!id) return;

    if (selected) selected.classList.remove("is-selected");
    group.classList.add("is-selected");
    selected = group;

    const resource = result.spec.resources.find((r) => r.id === id);
    if (resource) onSelectResource?.(resource);
  }

  for (const group of stage.querySelectorAll("[data-resource-id]")) {
    group.addEventListener("click", (event) => {
      event.stopPropagation();
      selectNode(group);
    });
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(group);
      } else if (event.key === "f") {
        event.preventDefault();
        filters.focus(group.dataset.resourceId);
        filterBar.paintFocus();
      }
    });
    // Double click focuses the dependency neighbourhood, which is the
    // question a reader usually has next after "what is this?".
    group.addEventListener("dblclick", (event) => {
      event.stopPropagation();
      filters.focus(group.dataset.resourceId);
      filterBar.paintFocus();
    });
  }

  /** Focus one resource from outside, e.g. from the resource list. */
  function highlight(resourceId) {
    const group = stage.querySelector(`[data-resource-id="${CSS.escape(resourceId)}"]`);
    if (group) selectNode(group);
  }

  /* ---------------- search, filters and focus ---------------- */

  const filters = createFilters({
    stage,
    result,
    onFocus: (id) => {
      // Focusing scrolls the subject into view; it never moves it.
      const group = stage.querySelector(`[data-resource-id="${CSS.escape(id)}"]`);
      if (!group) return;
      const box = group.getBBox?.();
      if (!box) return;
      const area = stage.getBoundingClientRect();
      state.x = area.width / 2 - (box.x + box.width / 2) * state.zoom;
      state.y = area.height / 2 - (box.y + box.height / 2) * state.zoom;
      userAdjusted = true;
      apply();
    },
  });

  const filterBar = renderFilterBar(filters, {
    onSelectResource: (resource) => {
      const group = stage.querySelector(
        `[data-resource-id="${CSS.escape(resource.id)}"]`);
      if (group) selectNode(group);
    },
  });

  /* ---------------- mini map ---------------- */

  // A second, scaled copy of the same SVG with a viewport rectangle over it.
  // Cloning is cheaper and always truthful: it cannot drift from the diagram
  // the way a separately-drawn schematic would, and it needs no extra data.
  const MINIMAP_W = 168;
  const minimap = el("div", { class: "minimap", "aria-hidden": "true" });
  const minimapFrame = el("div", { class: "minimap__frame" });
  let minimapScale = 1;

  function buildMinimap() {
    const { width, height } = contentSize();
    minimapScale = MINIMAP_W / width;
    const mapHeight = Math.round(height * minimapScale);

    minimap.style.width = `${MINIMAP_W}px`;
    minimap.style.height = `${mapHeight}px`;

    const clone = svg.cloneNode(true);
    clone.style.width = `${width}px`;
    clone.style.height = `${height}px`;
    clone.removeAttribute("tabindex");
    // The clone is decoration. Stripping the identifying attributes keeps it
    // out of every query that looks for a node or an edge -- without this the
    // filters bound to whichever copy the DOM returned last, which was the
    // mini map, and the real diagram never dimmed.
    for (const node of clone.querySelectorAll("[tabindex]")) node.removeAttribute("tabindex");
    for (const node of clone.querySelectorAll("[data-resource-id]")) {
      node.removeAttribute("data-resource-id");
      node.removeAttribute("role");
      node.removeAttribute("aria-label");
    }
    for (const path of clone.querySelectorAll("[data-from]")) {
      path.removeAttribute("data-from");
      path.removeAttribute("data-to");
    }

    const inner = el("div", { class: "minimap__inner" }, [clone]);
    inner.style.transform = `scale(${minimapScale})`;
    clear(minimap).append(inner, minimapFrame);
  }

  /** Draw the rectangle showing which part of the drawing is on screen. */
  function updateMinimap() {
    const area = stage.getBoundingClientRect();
    if (!area.width) return;
    const ratio = minimapScale / state.zoom;
    minimapFrame.style.width = `${Math.min(MINIMAP_W, area.width * ratio)}px`;
    minimapFrame.style.height = `${area.height * ratio}px`;
    minimapFrame.style.transform =
      `translate(${-state.x * minimapScale / state.zoom}px, ` +
      `${-state.y * minimapScale / state.zoom}px)`;
  }

  // Clicking the map jumps the view to that point.
  minimap.addEventListener("pointerdown", (event) => {
    const box = minimap.getBoundingClientRect();
    const area = stage.getBoundingClientRect();
    const targetX = (event.clientX - box.left) / minimapScale;
    const targetY = (event.clientY - box.top) / minimapScale;
    state.x = area.width / 2 - targetX * state.zoom;
    state.y = area.height / 2 - targetY * state.zoom;
    userAdjusted = true;
    apply();
  });

  /* ---------------- refit on size change ---------------- */

  // A tab panel that is hidden has no size, so the first fit has to wait for
  // the element to actually occupy space. ResizeObserver covers the tab
  // becoming visible, the window resizing, and the sidebar collapsing, which
  // a window resize listener alone would miss.
  let fitted = false;
  function onStageResized() {
    if (!fitted) { fitted = fit({ animate: false }); return; }
    // Refit an untouched view so a narrower window never clips the drawing;
    // keep a deliberately chosen one, just re-centred.
    if (userAdjusted) centre({ animate: false });
    else fit({ animate: false });
  }

  const observer = new ResizeObserver(onStageResized);
  observer.observe(stage);

  // A window listener as well: ResizeObserver does not deliver while a tab is
  // throttled, and the two together cover every way the stage can change size.
  const onWindowResize = () => onStageResized();
  window.addEventListener("resize", onWindowResize);

  // The observer alone is not enough: at construction the stage is still
  // detached, so it has no box to report and the first callback never
  // arrives. Retry on animation frames until the element has been laid out,
  // then stop. This is what guarantees nothing is clipped on first paint.
  // Retried on a timer rather than only on animation frames: a background or
  // hidden tab throttles requestAnimationFrame indefinitely, and the diagram
  // must still be fitted when that tab is brought forward.
  let attempts = 0;
  (function attemptInitialFit() {
    if (fitted || attempts > 40) return;
    attempts += 1;
    fitted = fit({ animate: false });
    if (!fitted) setTimeout(attemptInitialFit, 50);
  })();

  /* ---------------- exports ---------------- */

  // Every export starts from a themed SVG rendered by the backend, not from
  // the copy on screen. The on-screen document carries a prefers-color-scheme
  // query; exporting it would hand someone a figure that inverts itself on a
  // dark-themed machine, which is a defect in a paper rather than a taste.
  async function themedSvg(theme, transparent = false) {
    return api.exportDiagram(result.spec.prompt, { theme, transparent });
  }

  async function exportSvg(theme) {
    try {
      const svg = await themedSvg(theme);
      downloadText(svg, `${result.summary.name}-${theme}.svg`, "image/svg+xml");
      toast(`SVG saved (${theme})`, { variant: "success" });
    } catch (error) {
      toast("SVG export failed", { message: error.message, variant: "error" });
    }
  }

  /**
   * Rasterise the themed SVG at a given scale.
   *
   * Done in the browser because a server-side rasteriser means a native
   * dependency — the same reason this project has no Graphviz — which would
   * also break the serverless deployment. The vector source comes from the
   * backend, so only the pixels are produced here.
   */
  function rasterise(svgText, scale, transparent) {
    return new Promise((resolve, reject) => {
      const { width, height } = contentSize();
      const image = new Image();
      const url = URL.createObjectURL(
        new Blob([svgText], { type: "image/svg+xml;charset=utf-8" }),
      );
      image.onload = () => {
        const canvas = el("canvas", {
          width: Math.round(width * scale),
          height: Math.round(height * scale),
        });
        const context = canvas.getContext("2d");
        if (!transparent) {
          // The SVG paints its own ground, but a transparent PNG dropped into
          // a light document would be unreadable, so fill first.
          context.fillStyle = svgText.includes('fill: #0f141b') ? "#0f141b" : "#ffffff";
          context.fillRect(0, 0, canvas.width, canvas.height);
        }
        context.setTransform(scale, 0, 0, scale, 0, 0);
        context.drawImage(image, 0, 0);
        URL.revokeObjectURL(url);
        resolve(canvas);
      };
      image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("raster failed")); };
      image.src = url;
    });
  }

  async function exportPng(theme, scale, transparent = false) {
    try {
      const svg = await themedSvg(theme, transparent);
      const canvas = await rasterise(svg, scale, transparent);
      canvas.toBlob((png) => {
        if (!png) { toast("PNG export failed", { variant: "error" }); return; }
        const suffix = transparent ? "transparent" : theme;
        const link = el("a", {
          href: URL.createObjectURL(png),
          download: `${result.summary.name}-${suffix}@${scale}x.png`,
        });
        document.body.append(link);
        link.click();
        link.remove();
        toast(`PNG saved at ${scale}×`, {
          message: `${canvas.width}×${canvas.height} pixels`,
          variant: "success",
        });
      }, "image/png");
    } catch (error) {
      toast("PNG export failed", {
        message: "Save the SVG instead — it is vector at any size.",
        variant: "error",
      });
    }
  }

  /**
   * The architecture report, opened in a print window.
   *
   * Printing keeps text selectable and the diagram vector at any zoom. A
   * hand-rolled PDF would embed a raster and lose both, and would need a
   * dependency this project has deliberately avoided.
   */
  async function exportReport() {
    try {
      const document_ = await api.exportReport(result.spec.prompt);
      const frame = el("iframe", {
        style: { position: "fixed", right: "0", bottom: "0",
                 width: "0", height: "0", border: "0" },
        "aria-hidden": "true",
      });
      document.body.append(frame);
      frame.contentDocument.open();
      frame.contentDocument.write(document_);
      frame.contentDocument.close();

      frame.contentWindow.addEventListener("afterprint", () => frame.remove(), { once: true });
      setTimeout(() => {
        frame.contentWindow.focus();
        frame.contentWindow.print();
        setTimeout(() => frame.isConnected && frame.remove(), 60000);
      }, 250);

      toast("Report ready", {
        message: 'Choose "Save as PDF" in the print dialog for a vector file.',
        variant: "info",
      });
    } catch (error) {
      toast("Report failed", { message: error.message, variant: "error" });
    }
  }

  /** The report in a tab, for reading rather than printing. */
  async function openReport() {
    try {
      const document_ = await api.exportReport(result.spec.prompt);
      const blob = new Blob([document_], { type: "text/html" });
      window.open(URL.createObjectURL(blob), "_blank", "noopener");
    } catch (error) {
      toast("Report failed", { message: error.message, variant: "error" });
    }
  }

  /* ---------------- toolbar ---------------- */

  const zoomLabel = el("span", { class: "zoom-label tabular", text: "100%" });

  function menuItem(label, hint, handler) {
    return el("button", {
      class: "menu__item menu__item--export",
      role: "menuitem",
      type: "button",
      onClick: () => { closeMenu(); handler(); },
    }, [
      icon("download", 14),
      el("span", { class: "menu__item-body" }, [
        el("span", { text: label }),
        hint && el("span", { class: "menu__item-hint", text: hint }),
      ]),
    ]);
  }

  const exportMenu = el("div", { class: "menu menu--export", hidden: true, role: "menu" }, [
    el("div", { class: "menu__label", text: "Document" }),
    menuItem("Architecture report", "Print or save as PDF", exportReport),
    menuItem("Open report in a tab", "Read without printing", openReport),

    el("div", { class: "menu__separator", role: "separator" }),
    el("div", { class: "menu__label", text: "Vector" }),
    menuItem("SVG · light", "For documents and slides", () => exportSvg("light")),
    menuItem("SVG · dark", "For dark presentations", () => exportSvg("dark")),
    menuItem("SVG · print", "High contrast, greyscale-safe", () => exportSvg("print")),

    el("div", { class: "menu__separator", role: "separator" }),
    el("div", { class: "menu__label", text: "Image" }),
    menuItem("PNG · 2×", "Screens and slides", () => exportPng("light", 2)),
    menuItem("PNG · 4×", "Print quality, about 300 dpi", () => exportPng("light", 4)),
    menuItem("PNG · dark 2×", "For dark presentations", () => exportPng("dark", 2)),
    menuItem("PNG · transparent", "Composites onto any ground",
             () => exportPng("light", 3, true)),

    el("div", { class: "menu__separator", role: "separator" }),
    el("div", { class: "menu__label", text: "Source" }),
    menuItem("Mermaid", "Text, for version control", () => {
      downloadText(result.diagram.mermaid, `${result.summary.name}.mmd`);
      toast("Mermaid saved", { variant: "success" });
    }),
  ]);

  const exportButton = el("button", {
    class: "btn btn--secondary btn--sm",
    type: "button",
    "aria-haspopup": "menu",
    "aria-expanded": "false",
    onClick: (event) => { event.stopPropagation(); toggleMenu(); },
  }, [icon("download", 14), el("span", { text: "Export" })]);

  function toggleMenu() {
    const open = exportMenu.hidden;
    exportMenu.hidden = !open;
    exportButton.setAttribute("aria-expanded", String(open));
    if (open) exportMenu.querySelector(".menu__item").focus();
  }
  function closeMenu() {
    exportMenu.hidden = true;
    exportButton.setAttribute("aria-expanded", "false");
  }
  document.addEventListener("click", closeMenu);
  exportMenu.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closeMenu(); exportButton.focus(); }
  });

  function control(label, tooltip, handler, children) {
    return el("button", {
      class: "btn btn--ghost btn--sm",
      type: "button",
      "data-tooltip": tooltip,
      "aria-label": label,
      onClick: handler,
    }, children || [el("span", { text: label })]);
  }

  const toolbar = el("div", { class: "toolbar" }, [
    el("div", { class: "segmented", role: "group", "aria-label": "Diagram format" }, [
      el("button", { class: "segmented__option", type: "button", "aria-pressed": "true",
        onClick: (e) => setFormat(e, true) }, [el("span", { text: "Diagram" })]),
      el("button", { class: "segmented__option", type: "button", "aria-pressed": "false",
        onClick: (e) => setFormat(e, false) }, [el("span", { text: "Mermaid" })]),
    ]),

    el("span", { class: "toolbar__divider" }),

    control("Zoom out", "Zoom out  −", () => zoomTo(state.zoom / 1.2), [icon("x", 1), el("span", { text: "−" })]),
    zoomLabel,
    control("Zoom in", "Zoom in  +", () => zoomTo(state.zoom * 1.2), [el("span", { text: "+" })]),
    control("Reset zoom", "Reset to 100%", () => { state.zoom = 1; centre(); }),

    el("span", { class: "toolbar__divider" }),

    control("Fit to screen", "Fit to screen  0", () => fit(), [icon("panel", 14), el("span", { text: "Fit" })]),
    control("Centre diagram", "Centre", () => centre(), [icon("route", 14), el("span", { text: "Centre" })]),

    el("span", { class: "toolbar__spacer" }),

    control("Toggle mini map", "Mini map", () => {
      minimap.classList.toggle("is-hidden");
    }, [icon("panel", 14)]),
    control("Fullscreen", "Fullscreen", toggleFullscreen, [icon("layers", 14)]),
    el("div", { class: "menu-anchor" }, [exportButton, exportMenu]),
  ]);

  function setFormat(event, showDiagram) {
    for (const option of event.currentTarget.parentElement.children) {
      option.setAttribute("aria-pressed", String(option === event.currentTarget));
    }
    stage.hidden = !showDiagram;
    mermaidView.hidden = showDiagram;
    if (showDiagram) requestAnimationFrame(() => fit({ animate: false }));
  }

  stage.append(minimap);
  const wrapper = el("div", { class: "diagram" }, [
    toolbar, filterBar.element, stage, mermaidView,
  ]);

  // Built once the SVG exists; the frame is positioned by the first fit.
  if (svg) buildMinimap();

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      wrapper.requestFullscreen?.()
        .then(() => requestAnimationFrame(() => fit()))
        .catch(() => toast("Fullscreen unavailable", { variant: "warning" }));
    }
  }
  document.addEventListener("fullscreenchange", () => {
    requestAnimationFrame(() => fit());
  });

  return {
    element: wrapper,
    fit,
    centre,
    highlight,
    filters,
    destroy: () => {
      observer.disconnect();
      window.removeEventListener("resize", onWindowResize);
      document.removeEventListener("click", closeMenu);
    },
  };
}
