/**
 * Terraform viewer.
 *
 * Split out of results.js, which had grown past 900 lines. The viewer owns
 * three concerns that belong together — highlighting, folding and block
 * navigation — and nothing else in the results workspace needs them.
 *
 * Highlighting goes through the tokeniser in hcl.js, which returns data
 * rather than markup so every token reaches the DOM as a text node. The file
 * being displayed was generated from a user's prompt; an innerHTML shortcut
 * here would be an injection the moment a prompt contained markup.
 */

import { findBlocks, tokeniseLine } from "./hcl.js";
import { clear, copyText, downloadText, el, icon } from "./ui.js";

/** Reading order for the generated project, not alphabetical. */
const FILE_ORDER = [
  "versions.tf", "variables.tf", "locals.tf", "network.tf", "security.tf",
  "compute.tf", "data.tf", "edge.tf", "integration.tf", "iam.tf",
  "monitoring.tf", "outputs.tf", "terraform.tfvars",
];

export function renderTerraform(result) {
  const names = Object.keys(result.terraform).sort((a, b) => {
    const ia = FILE_ORDER.indexOf(a);
    const ib = FILE_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b);
  });

  let active = names[0];
  let query = "";
  /** Start-line numbers of collapsed blocks. Reset when the file changes. */
  let folded = new Set();

  const fileList = el("ul", { class: "file-list", role: "tablist", "aria-label": "Generated files" });
  const navList = el("ul", { class: "block-list", "aria-label": "Blocks in this file" });
  const viewer = el("div", { class: "viewer" });
  const meta = el("span", { class: "toolbar__meta" });

  const search = el("input", {
    class: "field field--inline",
    type: "search",
    placeholder: "Search in file…",
    "aria-label": "Search within the file",
  });
  search.addEventListener("input", () => { query = search.value; paintViewer(); });

  const blocks = () => findBlocks(result.terraform[active] || "");

  /* ---------------- file list ---------------- */

  function paintList() {
    clear(fileList);
    for (const name of names) {
      const lines = result.terraform[name].split("\n").length;
      fileList.append(
        el("li", {}, [
          el("button", {
            class: "file-item",
            type: "button",
            role: "tab",
            "aria-selected": String(name === active),
            onClick: () => {
              active = name;
              search.value = "";
              query = "";
              folded = new Set();
              paintList();
              paintNav();
              paintViewer();
            },
          }, [
            icon(name.endsWith(".tf") ? "code" : "book", 13),
            el("span", { class: "file-item__name mono", text: name }),
            el("span", { class: "file-item__lines tabular", text: String(lines) }),
          ]),
        ]),
      );
    }
  }

  /* ---------------- block navigation ---------------- */

  function paintNav() {
    clear(navList);
    const found = blocks();
    if (!found.length) {
      navList.append(el("li", { class: "block-list__empty", text: "No blocks in this file" }));
      return;
    }
    for (const block of found) {
      navList.append(
        el("li", {}, [
          el("button", {
            class: "block-item",
            type: "button",
            title: `${block.kind} ${block.type} ${block.name}`.trim(),
            onClick: () => jumpTo(block.start),
          }, [
            el("span", { class: `block-item__kind kind--${block.kind}`, text: block.kind }),
            el("span", { class: "block-item__name mono", text: block.name }),
            el("span", { class: "block-item__line tabular", text: String(block.start + 1) }),
          ]),
        ]),
      );
    }
  }

  /** Scroll to a line and flash it, so the jump is visible rather than silent. */
  function jumpTo(lineIndex) {
    // Expand anything hiding the target, or the scroll lands on nothing.
    for (const block of blocks()) {
      if (folded.has(block.start) && lineIndex > block.start && lineIndex <= block.end) {
        folded.delete(block.start);
      }
    }
    paintViewer();

    const target = viewer.querySelector(`[data-line="${lineIndex}"]`);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("is-flash");
    setTimeout(() => target.classList.remove("is-flash"), 900);
  }

  /* ---------------- code surface ---------------- */

  function paintViewer() {
    const content = result.terraform[active] || "";
    const lines = content.split("\n");
    const needle = query.trim().toLowerCase();
    let hits = 0;

    const found = blocks();
    const startsAt = new Map(found.map((block) => [block.start, block]));

    // Lines a collapsed block hides. The gutter and the code skip exactly the
    // same set, which is what keeps line numbers aligned with their code.
    const hidden = new Set();
    for (const block of found) {
      if (!folded.has(block.start)) continue;
      for (let line = block.start + 1; line <= block.end; line += 1) hidden.add(line);
    }

    const gutter = el("div", { class: "viewer__gutter" });
    const code = el("div", { class: "viewer__code" });

    lines.forEach((line, index) => {
      const isHit = needle && line.toLowerCase().includes(needle);
      if (isHit) hits += 1;
      if (hidden.has(index)) return;

      const block = startsAt.get(index);
      const collapsed = block ? folded.has(index) : false;

      gutter.append(
        el("span", { class: "viewer__gutter-row" }, [
          block
            ? el("button", {
                class: "viewer__fold",
                type: "button",
                "aria-expanded": String(!collapsed),
                "aria-label": `${collapsed ? "Expand" : "Collapse"} ${block.kind} ${block.name}`,
                onClick: () => {
                  if (collapsed) folded.delete(index);
                  else folded.add(index);
                  syncFoldAll();
                  paintViewer();
                },
              }, [el("span", { class: "viewer__fold-caret", "aria-hidden": "true" })])
            : el("span", { class: "viewer__fold-spacer", "aria-hidden": "true" }),
          el("span", { class: "viewer__line-no", "aria-hidden": "true", text: String(index + 1) }),
        ]),
      );

      const lineEl = el("span", {
        class: `viewer__line${isHit ? " is-hit" : ""}`,
        dataset: { line: String(index) },
      });

      for (const token of tokeniseLine(line)) {
        lineEl.append(
          token.type === "plain"
            ? document.createTextNode(token.text)
            : el("span", { class: `tok tok--${token.type}`, text: token.text }),
        );
      }
      if (!line) lineEl.append(document.createTextNode(" "));

      if (collapsed) {
        lineEl.append(
          el("span", {
            class: "viewer__folded-hint",
            text: ` ⋯ ${block.end - block.start} more lines`,
          }),
        );
      }
      code.append(lineEl);
    });

    meta.textContent = needle
      ? `${hits} match${hits === 1 ? "" : "es"}`
      : `${lines.length} lines`;

    clear(viewer).append(gutter, code);
  }

  /* ---------------- assembly ---------------- */

  // The label states what the button will do, not what it did. A control
  // reading "Fold all" that unfolds is a small lie the user has to test for.
  const foldAllLabel = el("span", { text: "Fold all" });
  const foldAllButton = el("button", {
    class: "btn btn--ghost btn--sm",
    type: "button",
    onClick: () => {
      folded = folded.size ? new Set() : new Set(blocks().map((b) => b.start));
      syncFoldAll();
      paintViewer();
    },
  }, [icon("layers", 14), foldAllLabel]);

  function syncFoldAll() {
    foldAllLabel.textContent = folded.size ? "Unfold all" : "Fold all";
  }

  paintList();
  paintNav();
  paintViewer();
  syncFoldAll();

  return el("div", { class: "stack" }, [
    el("div", { class: "toolbar" }, [
      search,
      meta,
      el("span", { class: "toolbar__spacer" }),
      foldAllButton,
      el("button", {
        class: "btn btn--ghost btn--sm",
        type: "button",
        onClick: () => copyText(result.terraform[active], `${active} copied`),
      }, [icon("copy", 14), el("span", { text: "Copy file" })]),
      el("button", {
        class: "btn btn--secondary btn--sm",
        type: "button",
        onClick: () => downloadText(result.terraform[active], active),
      }, [icon("download", 14), el("span", { text: "Download" })]),
    ]),
    el("div", { class: "split split--code" }, [
      el("div", { class: "code-sidebar" }, [
        el("div", { class: "code-sidebar__label", text: "Files" }),
        fileList,
        el("div", { class: "code-sidebar__label", text: "Blocks" }),
        navList,
      ]),
      viewer,
    ]),
  ]);
}
