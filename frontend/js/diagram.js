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

import { downloadText, el, icon, toast } from "./ui.js";

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
      }
    });
  }

  /** Focus one resource from outside, e.g. from the resource list. */
  function highlight(resourceId) {
    const group = stage.querySelector(`[data-resource-id="${CSS.escape(resourceId)}"]`);
    if (group) selectNode(group);
  }

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

  function exportSvg() {
    downloadText(result.diagram.svg, `${result.summary.name}-architecture.svg`, "image/svg+xml");
    toast("SVG saved", { variant: "success" });
  }

  function rasterise(scale = 2) {
    return new Promise((resolve, reject) => {
      const { width, height } = contentSize();
      const image = new Image();
      const url = URL.createObjectURL(
        new Blob([result.diagram.svg], { type: "image/svg+xml;charset=utf-8" }),
      );
      image.onload = () => {
        const canvas = el("canvas", { width: width * scale, height: height * scale });
        const context = canvas.getContext("2d");
        // The exported SVG paints its own background, but a transparent PNG
        // dropped into a light document would be unreadable, so fill first.
        context.fillStyle = getComputedStyle(document.body).backgroundColor;
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.scale(scale, scale);
        context.drawImage(image, 0, 0);
        URL.revokeObjectURL(url);
        resolve(canvas);
      };
      image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("raster failed")); };
      image.src = url;
    });
  }

  async function exportPng() {
    try {
      const canvas = await rasterise(2);
      canvas.toBlob((png) => {
        const link = el("a", {
          href: URL.createObjectURL(png),
          download: `${result.summary.name}-architecture.png`,
        });
        document.body.append(link);
        link.click();
        link.remove();
        toast("PNG saved", { variant: "success" });
      }, "image/png");
    } catch {
      toast("PNG export failed", { message: "Save the SVG instead.", variant: "error" });
    }
  }

  /**
   * PDF export goes through the browser's print pipeline.
   *
   * Hand-rolling a PDF would mean embedding a raster and losing the vector
   * output entirely; printing keeps the diagram sharp at any zoom and adds no
   * dependency. The one cost is that the user picks "Save as PDF" in the
   * dialog rather than getting a file immediately, which the button label
   * says plainly.
   */
  function exportPdf() {
    const frame = el("iframe", {
      style: { position: "fixed", right: "0", bottom: "0", width: "0", height: "0", border: "0" },
      "aria-hidden": "true",
    });
    document.body.append(frame);

    const doc = frame.contentDocument;
    doc.open();
    doc.write(
      `<!DOCTYPE html><html><head><title>${result.summary.name} architecture</title>` +
      `<style>@page{size:landscape;margin:12mm}` +
      `body{margin:0;display:grid;place-items:center;height:100vh}` +
      `svg{max-width:100%;max-height:100%}</style></head><body>` +
      result.diagram.svg +
      `</body></html>`,
    );
    doc.close();

    frame.contentWindow.addEventListener("afterprint", () => frame.remove(), { once: true });
    setTimeout(() => {
      frame.contentWindow.focus();
      frame.contentWindow.print();
      // Some browsers never fire afterprint; clean up regardless.
      setTimeout(() => frame.isConnected && frame.remove(), 60000);
    }, 120);

    toast("Print dialog opened", {
      message: 'Choose "Save as PDF" as the destination for a vector file.',
      variant: "info",
    });
  }

  /* ---------------- toolbar ---------------- */

  const zoomLabel = el("span", { class: "zoom-label tabular", text: "100%" });

  const exportMenu = el("div", { class: "menu", hidden: true, role: "menu" }, [
    el("button", { class: "menu__item", role: "menuitem", type: "button",
      onClick: () => { closeMenu(); exportPng(); } },
      [icon("download", 14), el("span", { text: "PNG image" })]),
    el("button", { class: "menu__item", role: "menuitem", type: "button",
      onClick: () => { closeMenu(); exportSvg(); } },
      [icon("download", 14), el("span", { text: "SVG vector" })]),
    el("button", { class: "menu__item", role: "menuitem", type: "button",
      onClick: () => { closeMenu(); exportPdf(); } },
      [icon("download", 14), el("span", { text: "PDF via print…" })]),
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

  const wrapper = el("div", { class: "diagram" }, [toolbar, stage, mermaidView]);

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
    destroy: () => {
      observer.disconnect();
      window.removeEventListener("resize", onWindowResize);
      document.removeEventListener("click", closeMenu);
    },
  };
}
