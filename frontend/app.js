/*
 * Front end for the AI-Driven Infrastructure Diagram and IaC Generator.
 *
 * No framework and no build step: the page talks to /api/generate and renders
 * the response. The SVG arrives ready to display, so the browser never needs a
 * diagramming library.
 */

const $ = (id) => document.getElementById(id);

const state = {
  result: null,
  activeFile: null,
  zoom: 1,
};

const el = {
  prompt: $("prompt"),
  generate: $("generate"),
  useLlm: $("use-llm"),
  status: $("status"),
  examples: $("examples"),
  empty: $("empty"),
  results: $("results"),
  summary: $("summary"),
  stage: $("diagram-stage"),
  mermaid: $("mermaid-code"),
  fileList: $("file-list"),
  fileContent: $("file-content"),
  fileHint: $("file-hint"),
  findings: $("findings"),
  findingsCount: $("findings-count"),
  assumptions: $("assumptions"),
  resourceList: $("resource-list"),
  specJson: $("spec-json"),
};

/* ---------- helpers ---------- */

function setStatus(message, kind = "") {
  el.status.textContent = message;
  el.status.className = `status ${kind}`;
}

function text(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

/* ---------- boot ---------- */

async function boot() {
  try {
    const [health, examples] = await Promise.all([
      fetch("/api/health").then((r) => r.json()),
      fetch("/api/examples").then((r) => r.json()),
    ]);

    if (!health.llm_available) {
      el.useLlm.disabled = true;
      el.useLlm.parentElement.title =
        "Set ANTHROPIC_API_KEY and install the anthropic package to enable LLM extraction.";
    }
    setStatus(`ready - ${health.extractor} extractor - v${health.version}`);

    examples.forEach((example) => {
      const chip = text("button", "chip", example.title);
      chip.addEventListener("click", () => {
        el.prompt.value = example.prompt;
        el.prompt.focus();
      });
      el.examples.appendChild(chip);
    });
  } catch (error) {
    setStatus("backend unreachable", "error");
  }
}

/* ---------- generation ---------- */

async function generate() {
  const prompt = el.prompt.value.trim();
  if (prompt.length < 3) {
    setStatus("describe the infrastructure you need first", "error");
    el.prompt.focus();
    return;
  }

  el.generate.disabled = true;
  setStatus("generating...", "busy");

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        prompt,
        extractor: el.useLlm.checked ? "llm" : "rule",
      }),
    });

    const body = await response.json();
    if (!response.ok) {
      setStatus(body.detail || "generation failed", "error");
      return;
    }

    state.result = body;
    state.zoom = 1;
    render(body);
    setStatus(
      `${body.summary.resource_count} resources - ${body.summary.file_count} files - ` +
      `${Math.round(body.summary.duration_ms)} ms`
    );
  } catch (error) {
    setStatus(`request failed: ${error.message}`, "error");
  } finally {
    el.generate.disabled = false;
  }
}

/* ---------- rendering ---------- */

function render(result) {
  el.empty.hidden = true;
  el.results.hidden = false;

  renderSummary(result.summary);
  renderDiagram(result.diagram);
  renderTerraform(result.terraform);
  renderFindings(result.findings, result.spec.assumptions || []);
  renderSpec(result.spec);
}

function renderSummary(summary) {
  el.summary.replaceChildren();
  const cards = [
    ["Region", summary.region],
    ["Environment", summary.environment],
    ["Resources", summary.resource_count],
    ["Files", summary.file_count],
    ["Est. cost / mo", `$${Math.round(summary.estimated_monthly_cost_usd)}`],
    ["Extractor", summary.extractor],
  ];
  cards.forEach(([label, value]) => {
    const card = text("div", "stat");
    card.append(text("b", null, String(value)), text("span", null, label));
    el.summary.appendChild(card);
  });

  const description = text("div", "stat wide");
  description.append(
    text("b", null, summary.description || "-"),
    text("span", null, "what was understood")
  );
  el.summary.appendChild(description);
}

function renderDiagram(diagram) {
  el.stage.innerHTML = diagram.svg;
  el.mermaid.textContent = diagram.mermaid;
  applyZoom();
}

function applyZoom() {
  const svg = el.stage.querySelector("svg");
  if (svg) svg.style.transform = `scale(${state.zoom})`;
}

function renderTerraform(files) {
  el.fileList.replaceChildren();
  const names = Object.keys(files).sort(sortFiles);

  names.forEach((name) => {
    const item = text("li", null, name);
    item.addEventListener("click", () => selectFile(name));
    el.fileList.appendChild(item);
  });

  if (names.length) selectFile(names.includes("main.tf") ? "main.tf" : names[0]);
}

function sortFiles(a, b) {
  // Show the files a reader opens first, first.
  const order = ["versions.tf", "variables.tf", "locals.tf", "network.tf",
    "security.tf", "compute.tf", "data.tf", "edge.tf", "integration.tf",
    "iam.tf", "monitoring.tf", "outputs.tf"];
  const ia = order.indexOf(a);
  const ib = order.indexOf(b);
  if (ia !== -1 && ib !== -1) return ia - ib;
  if (ia !== -1) return -1;
  if (ib !== -1) return 1;
  return a.localeCompare(b);
}

function selectFile(name) {
  state.activeFile = name;
  el.fileContent.textContent = state.result.terraform[name];
  el.fileHint.textContent = `${name} - ${state.result.terraform[name].split("\n").length} lines`;
  [...el.fileList.children].forEach((li) => {
    li.classList.toggle("active", li.textContent === name);
  });
}

function renderFindings(findings, assumptions) {
  el.findings.replaceChildren();
  const errors = findings.filter((f) => f.severity === "error").length;
  el.findingsCount.textContent = findings.length || "0";
  el.findingsCount.className = errors ? "pill alert" : "pill";

  if (!findings.length) {
    el.findings.appendChild(
      text("li", null, "No policy or structural issues found in this design.")
    );
  }

  const rank = { error: 0, warning: 1, info: 2 };
  [...findings]
    .sort((a, b) => rank[a.severity] - rank[b.severity])
    .forEach((finding) => {
      const item = document.createElement("li");
      item.append(text("span", `sev ${finding.severity}`, finding.severity));
      const body = document.createElement("div");
      body.append(text("div", null, finding.message));
      const meta = finding.resource_id
        ? `${finding.code} - ${finding.resource_id}`
        : finding.code;
      body.append(text("code", null, meta));
      item.appendChild(body);
      el.findings.appendChild(item);
    });

  el.assumptions.replaceChildren();
  (assumptions.length ? assumptions : ["None recorded."]).forEach((note) => {
    el.assumptions.appendChild(text("li", null, note));
  });
}

function renderSpec(spec) {
  el.resourceList.replaceChildren();
  spec.resources.forEach((resource) => {
    const item = document.createElement("li");
    const label = resource.count > 1 ? `${resource.id} x${resource.count}` : resource.id;
    item.append(
      text("span", null, label),
      text("span", `tag ${resource.origin}`, resource.origin)
    );
    item.title = `${resource.name} (${resource.kind})` +
      (resource.evidence ? `\nfrom: "${resource.evidence}"` : "");
    el.resourceList.appendChild(item);
  });
  el.specJson.textContent = JSON.stringify(spec, null, 2);
}

/* ---------- tabs and controls ---------- */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    ["diagram", "terraform", "findings", "spec"].forEach((name) => {
      $(`panel-${name}`).hidden = name !== tab.dataset.tab;
    });
  });
});

document.querySelectorAll(".seg-btn").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
    button.classList.add("active");
    const showSvg = button.dataset.view === "svg";
    el.stage.hidden = !showSvg;
    el.mermaid.hidden = showSvg;
  });
});

$("zoom-in").addEventListener("click", () => {
  state.zoom = Math.min(2.5, state.zoom + 0.15);
  applyZoom();
});

$("zoom-out").addEventListener("click", () => {
  state.zoom = Math.max(0.4, state.zoom - 0.15);
  applyZoom();
});

$("download-svg").addEventListener("click", () => {
  if (!state.result) return;
  const blob = new Blob([state.result.diagram.svg], { type: "image/svg+xml" });
  triggerDownload(blob, `${state.result.summary.name}-architecture.svg`);
});

$("copy-file").addEventListener("click", async () => {
  if (!state.activeFile) return;
  await navigator.clipboard.writeText(state.result.terraform[state.activeFile]);
  setStatus(`copied ${state.activeFile}`);
});

$("download-zip").addEventListener("click", async () => {
  if (!state.result) return;
  setStatus("packaging...", "busy");
  const response = await fetch("/api/generate/download", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      prompt: el.prompt.value.trim(),
      extractor: el.useLlm.checked ? "llm" : "rule",
    }),
  });
  if (!response.ok) {
    setStatus("packaging failed", "error");
    return;
  }
  triggerDownload(await response.blob(), `${state.result.summary.name}.zip`);
  setStatus("project downloaded");
});

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

el.generate.addEventListener("click", generate);
el.prompt.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") generate();
});

boot();
