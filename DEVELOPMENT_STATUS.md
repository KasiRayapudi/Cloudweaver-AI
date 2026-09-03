# Development status

Handoff document. Written 2026-08-29, immediately after Priority 1 of the
demo pass was committed and while Priority 2 was part-started.

Read the **Exact next implementation step** section at the bottom first — it
tells you precisely where the work stopped and what to do next.

---

## 1. Current milestone

**Demo polish pass: all six priorities complete.**

The work is organised as six demo priorities:

| # | Priority | State |
|---|---|---|
| 1 | Diagram viewer | **Complete** — `a5fc56b` |
| 2 | Terraform viewer | **Complete** — `2eda2b7` |
| 3 | Cost dashboard | **Complete** — `26d3dd9` |
| 4 | Validation dashboard | **Complete** — `9dfdaa1` |
| 5 | Overall UI polish | **Complete** — `eb0e036` |
| 6 | Navigation and top bar | **Complete** — `eb0e036` |

Last commit: `eb0e036`. Working tree clean, 580 tests pass, ruff clean,
17/17 projects pass `terraform validate`.

### Added after the handoff was first written

- Terraform viewer: syntax highlighting, folding, block navigation
  (`frontend/js/terraform-view.js`, `frontend/js/hcl.js`)
- Cost dashboard: daily/annual cards, donut by service family,
  largest-cost callout (`frontend/js/cost-view.js`)
- Validation dashboard: verdict strip, expandable findings, suggested fixes
  for all 32 backend finding codes (`frontend/js/validation-view.js`)
- Dark default, settings and status menus, rotating placeholders,
  architecture summary bar above the diagram
- **Stale-asset fix**: static assets now send `no-cache, must-revalidate`
  and asset URLs carry `?v=2`. Bump the stamp when shipping frontend
  changes. See "Remaining bugs" for the one caveat.

`results.js` went from 900 lines to 693 as the three views moved out.

---

## 2. Completed UI improvements

### Design system (commit `e7c7c2b`)
- `styles/tokens.css` — every colour, size, weight and duration is a token.
  No component holds a literal. All three theme states are handled: explicit
  light, explicit dark, and the un-stamped system default that only
  `prefers-color-scheme` can resolve.
- Neutrals are biased toward the brand hue; a pure grey beside a saturated
  accent reads as unconsidered.
- Elevation is a five-step ramp, not one shadow reused everywhere.

### Application shell (commit `e7c7c2b`)
- Sidebar with navigation, recent designs, favourites; collapsible; state
  persisted.
- Top bar with command palette trigger and LLM toggle.
- Command palette (Ctrl/Cmd+K) over commands, templates and history.
- Toasts, skeletons, empty and error states.
- **History persists in `localStorage`** — previously a refresh lost all work.

### Compose page (uncommitted at `e7c7c2b`, committed in `495b752`)
Measured before/after at 1440×900:

| Metric | Before | After |
|---|---|---|
| Header + composer share of viewport | 66% | 32.6% |
| Primary action position | y=629 | y=325 |
| First-screen ink density | 22.9% | 26.7% |
| WCAG contrast failures | 9 | 0 |
| Desktop target-size failures | 2 | 0 |

- Hero replaced with a capability strip carrying real figures.
- Templates split into two featured cards plus six dense rows (eight
  identical cards gave no card the weight of a recommendation).
- Pipeline explainer strip and footer added.

### Results workspace (commit `e7c7c2b`)
Eight tabs over one response object: Overview, Resources, Architecture,
Terraform, Validation, Decision trace, Cost, Dependencies. The decision
trace and dependency graph were surfaced for the first time — the backend
had been computing and discarding them.

### Diagram experience (commits `495b752`, `a5fc56b`)
- Generation lands on the Architecture tab automatically.
- Fit-to-view computed from the SVG `viewBox`. **There is no diagram
  library**; the SVG arrives fully laid out from the Python layout engine, so
  there is no `fitView()` to call. The viewBox is the exact bounding box, so
  the fit is precise by construction.
- Pan by drag, Ctrl+wheel zoom anchored on the pointer, arrow-key pan,
  `+`/`-` zoom, `0` to fit.
- Controls: Fit, Centre, Zoom ±, Reset, Mini map toggle, Fullscreen, Export.
- Export menu: PNG, SVG, PDF (via the print pipeline, which keeps vector
  output), Mermaid.
- Mini map: a scaled clone of the same SVG with a viewport rectangle, click
  to jump. A clone cannot drift from the diagram it represents.
- Every node is clickable and keyboard-reachable, opening the resource
  inspector.
- Resize refits an untouched view but preserves a deliberately chosen one.

### Generation experience (commit `495b752`)
Six-stage progress pipeline replacing the skeleton. **The stages are not
polled** — the backend is a single call completing in tens of milliseconds
and there is no per-stage endpoint. Stages advance on a short timer purely
as an explanation of the pipeline, and all remaining stages are marked
complete the instant the real response lands. No artificial delay was added.
A Cancel control appears only while a request is in flight.

### Accessibility
- Audited against the running page, not assumed.
- WCAG 2.2: 0 contrast failures, 0 target-size failures at desktop and
  mobile, skip link, focus-visible throughout, ARIA live regions, roving tab
  focus, focus trap in the palette, `prefers-reduced-motion` honoured.
- Two real gaps were found and fixed: an unlabelled palette input and a
  decorative SVG without `aria-hidden`.

---

## 3. Completed backend improvements

All backend work predates the UI pass and is fully committed.

### Phase 1 — five critical defects (commit `50c1091`)
- **C1** Quantity parser read digits from inside numbers: "Windows Server
  2022" produced 22 instances and a phantom load balancer; "172.16.0.0/16"
  produced 16. Both sides of the digit branch are now fenced.
- **C2** Aurora emitted `aws_db_instance`, which AWS rejects at apply.
  Modelled as `Kind.SQL_CLUSTER` → `aws_rds_cluster` plus cluster instances.
- **C3** AWS name limits ignored; a descriptive prompt produced a
  42-character ALB name against a 32-character cap. `locals.tf` now computes
  a trimmed `name_short` via `substr`/`trimsuffix`.
- **C4** Negation and hedging ignored: "an EC2 instance without a load
  balancer" created a load balancer. Cues are matched before and after the
  phrase, with clause boundaries so a refusal cannot leak across a contrast.
- **C5** Duplicate resources dropped silently. Realistic duplicate kinds now
  loop, and `engine/emission.py` compares the finished Terraform back
  against the model and errors on any shortfall.

### Phase 2a — traffic and provider (commit `8603514`)
- Provider gating: an Azure or GCP request is refused with a clear 422
  instead of silently producing AWS Terraform.
- Three balancer kinds (ALB, NLB, GWLB) with correct listener protocols,
  target-group protocols and health-check shapes.
- HTTPS/TLS with ACM certificates as a conditional mandatory dependency;
  the plain HTTP listener becomes a 301 redirect rather than disappearing.

### Phase 2b — stated values (commit `0d50123`)
- Stated CIDRs, resource names, AMI ids honoured.
- External references (`vpc-0abc123`) are looked up with a `data` source and
  never created.
- Multi-region / multi-environment / multi-account / multi-VPC requests are
  detected and reported rather than silently collapsing.

### Diagram generator (commit `a5fc56b`, presentation only)
- Node geometry widened 178×68 → 200×78, gutters 34→44, band gap 78→96.
- Geometric service icons replace three-letter text badges. Deliberately not
  facsimiles of the AWS icon set — that artwork is trademarked, and a
  consistent hand-drawn set reads better at 16px.
- Connectors turn through quadratic arcs rather than right angles.
- `data-resource-id`, `tabindex` and `role` on each node group, which is what
  makes node clicking possible. Purely additive; four tests pin it.

**Nothing in the extraction, policy, dependency, Terraform or cost logic was
changed by the UI pass.**

---

## 4. Modified files

### Committed, current
```
backend/app/engine/constraints.py        AWS API constraints (names, AZ counts)
backend/app/engine/emission.py           model-vs-Terraform audit
backend/app/engine/policy.py             dependency rules as data
backend/app/engine/mapper.py             fixed-point closure over the policy
backend/app/engine/validator.py          structural + security + AWS checks
backend/app/nlp/catalog.py               services, lexicon, cues, providers
backend/app/nlp/rule_extractor.py        deterministic extraction
backend/app/nlp/llm_extractor.py         optional Claude path
backend/app/generators/terraform/        HCL writer + project generator
backend/app/generators/diagram/          layout, SVG renderer, Mermaid
backend/app/api/                         routes and schemas
scripts/tf_validate.py                   terraform validate harness

frontend/index.html                      250 lines
frontend/styles/tokens.css               282   design tokens
frontend/styles/base.css                 171   reset, a11y primitives
frontend/styles/components.css           458   buttons, cards, tabs, toasts
frontend/styles/layout.css               495   shell, sidebar, composer
frontend/styles/compose.css              298   compose page
frontend/styles/result.css               794   result workspace, diagram, minimap
frontend/js/api.js                        92   backend client
frontend/js/store.js                     138   state + localStorage
frontend/js/ui.js                        218   el(), icons, toasts, clipboard
frontend/js/templates.js                 113   template gallery data
frontend/js/palette.js                   143   command palette
frontend/js/diagram.js                   531   viewer: fit, pan, zoom, minimap
frontend/js/results.js                   900   eight tabs + inspector
frontend/js/app.js                       518   bootstrap and orchestration
```

### Uncommitted right now
```
 M backend/app/generators/diagram/svg_renderer.py   line-length lint fix only
?? frontend/js/hcl.js                               NEW, 155 lines, not yet used
```

`svg_renderer.py` holds a one-line reformat of the `"security"` icon path to
satisfy ruff E501. It is safe and should be committed.

`frontend/js/hcl.js` is a complete, syntactically valid HCL tokeniser that
**nothing imports yet**. It is inert — the application runs correctly with
it present.

---

## 5. Pending files

Files that will need editing to finish Priority 2:

| File | Change needed |
|---|---|
| `frontend/js/results.js` | `renderTerraform` must use the tokeniser, add folding and a resource jump list |
| `frontend/styles/result.css` | token colour classes, fold controls, navigation list |
| `frontend/js/hcl.js` | already written; may need tuning once visible |

---

## 6. Current git status

```
Branch:            main, in sync with origin/main
Last commit:       a5fc56b  Diagram viewer: icons, spacing, rounded routing, mini map
Unpushed commits:  none
Working tree:      1 modified file, 1 untracked file (listed in section 4)
```

Recent history:
```
a5fc56b  Diagram viewer: icons, spacing, rounded routing, mini map
495b752  UI: land on the architecture, fit it, and make every node inspectable
e7c7c2b  UI milestone 1: design system and application shell
0d50123  Phase 2b: honour the concrete values the requirement states
8603514  Phase 2a: TLS, three balancer types, honest provider handling
50c1091  Phase 1: all five critical defects fixed, verified with terraform validate
```

Remote: `https://github.com/KasiRayapudi/Cloudweaver-AI.git`

---

## 7. Last successful commit

**`a5fc56b`** — "Diagram viewer: icons, spacing, rounded routing, mini map".

Verified at that commit:
- 580 backend tests pass
- ruff clean
- 17/17 projects pass `terraform validate`
- Diagram fits at 35% on a 20-resource design, centred both axes, nothing
  clipped, 34 icon tiles rendered, mini map cloned, node click opens the
  inspector
- No console errors

---

## 8. Remaining Priority 1 tasks

Priority 1 is **complete**. Two items were interpreted rather than
implemented literally, and a reviewer may want them revisited:

1. **"Animated edges"** — not implemented. Moving dashes along connectors
   were judged a distraction on a static architecture diagram, and would
   fight `prefers-reduced-motion`. Deliberate omission, not an oversight.
2. **"Better AWS icons"** — implemented as original geometric marks, not the
   official AWS icon set, which is trademarked artwork. If the demo needs
   official icons, that is a licensing decision, not a code one.

---

## 9. Remaining bugs

### Known and open

1. **Resize refit is unverified in the harness.** The logic is correct — a
   dispatched `resize` event refits from 71% to 40% and re-centres — but CDP
   viewport emulation on a hidden browser pane does not dispatch `resize`,
   so end-to-end resize behaviour has never been observed. **Test this by
   hand before the demo: drag the window narrower and confirm the diagram
   refits.**

2. **Nothing has ever been seen visually.** Every verification in this
   project was DOM measurement, because the browser pane was hidden
   throughout. Measurements are real (fit percentages, contrast ratios,
   overflow, target sizes); *appearance* is entirely unverified. This is the
   single biggest risk to the demo.

3. **Stale server processes.** `pkill -f uvicorn` does not work in this Git
   Bash environment. Orphaned servers hold port 8099 and silently serve old
   Python, which twice made a working change look broken. Use the taskkill
   command in section 11.

4. **Module imports are not versioned.** `index.html` stamps `?v=2` on the
   entry point and the stylesheets, but `app.js` imports its siblings with
   bare paths (`./ui.js`). A browser that cached those modules *before* the
   `Cache-Control` fix landed considers them fresh forever and will not
   revalidate. **If the UI behaves like an older version, hard reload once
   (Ctrl+Shift+R).** Every browser loading the app from now on is correct
   automatically, because the header is present from its first fetch.

### Known limitations, by design

- Multi-region, multi-environment, multi-account and multi-VPC designs
  collapse to one; the extractor detects and warns rather than pretending.
- The generator still assumes one resource per kind in places. The emission
  audit makes any shortfall an error rather than silent, but does not remove
  the limitation.
- Cost figures are static per-service constants, not live pricing.

---

## 10. Remaining UI enhancements

Ordered as the demo priorities specify.

### Priority 2 — Terraform viewer (in progress)
- [x] Line numbers, search with match count, copy, download *(already live)*
- [ ] Syntax highlighting — tokeniser written, **not wired in**
- [ ] Collapse/fold resource blocks
- [ ] Resource navigation list (jump to block)
- [ ] Typography pass on the code surface

### Priority 3 — Cost dashboard
- [x] Monthly, annualised, per-resource bars *(already live)*
- [ ] Daily cost card
- [ ] Pie/donut chart
- [ ] "Most expensive resource" callout
- [ ] Service-wise cost cards

### Priority 4 — Validation dashboard
- [x] Severity grouping, colours, codes *(already live)*
- [ ] Suggested fixes per finding
- [ ] Expandable finding cards
- [ ] Icons per severity
- [ ] Click a finding to select the resource it names

### Priority 5 — Overall polish
- [ ] Skeleton loaders for tab switches
- [ ] Page transition animations
- [ ] Empty-state review across all eight tabs

### Priority 6 — Navigation
- [x] Sidebar, command palette, theme toggle, shortcuts *(already live)*
- [ ] Top bar: notifications, settings, profile menus
- [ ] Rotating placeholder examples in the composer
- [ ] Default to dark theme rather than following the OS
- [ ] Lazy tab rendering / memoisation

---

## 11. Commands to restart the project

### Kill stale servers first — important
`pkill` does not work here. Orphaned uvicorn processes hold port 8099 and
serve stale Python, which looks exactly like a broken change.

```bash
for pid in $(tasklist //FI "IMAGENAME eq python.exe" //FO CSV //NH | cut -d, -f2 | tr -d '"'); do taskkill //F //PID $pid; done
```

### Start the application

```bash
cd "C:/Users/KASI/OneDrive/Desktop/ai-infra-iac-generator/backend" && python -m uvicorn app.main:app --port 8099
```

Then open <http://127.0.0.1:8099>.

### Install dependencies

```bash
pip install -r requirements-dev.txt
```

`requirements.txt` is runtime-only because Vercel installs it into the
serverless bundle; `requirements-dev.txt` adds pytest, ruff and httpx.

---

## 12. Commands to run tests

### Full backend suite — 580 tests, about 2 seconds

```bash
cd "C:/Users/KASI/OneDrive/Desktop/ai-infra-iac-generator" && python -m pytest
```

### Lint

```bash
cd "C:/Users/KASI/OneDrive/Desktop/ai-infra-iac-generator" && python -m ruff check backend/ scripts/
```

### Terraform validation — 17 generated projects against the real binary

```bash
cd "C:/Users/KASI/OneDrive/Desktop/ai-infra-iac-generator" && python scripts/tf_validate.py
```

Terraform 1.15.8 is installed via winget at
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\Hashicorp.Terraform*\terraform.exe`.
The harness finds it there when it is not on `PATH`.

**Note:** the harness initialises one shared working directory and swaps each
project's files into it. Terraform copies the ~600 MB AWS provider into every
directory it initialises, and initialising one per project filled a 200 GB
disk during development.

### Coverage

```bash
cd "C:/Users/KASI/OneDrive/Desktop/ai-infra-iac-generator" && python -m pytest --cov=backend/app --cov-report=term-missing
```

Currently 96% of 3,083 statements.

### JavaScript syntax check

```bash
cd "C:/Users/KASI/OneDrive/Desktop/ai-infra-iac-generator" && for f in frontend/js/*.js; do node --check "$f"; done
```

There is no frontend build step. The application is dependency-free ES
modules served straight from `frontend/`, which is what keeps the Vercel
deployment zero-config.

---

## 13. Exact next implementation step

Work stopped mid-way through Priority 2. `frontend/js/hcl.js` exists and is
complete but **nothing imports it**.

### Step 1 — commit what is already safe

```bash
cd "C:/Users/KASI/OneDrive/Desktop/ai-infra-iac-generator"
git add backend/app/generators/diagram/svg_renderer.py
git commit -m "Wrap the security icon path to satisfy ruff E501"
```

### Step 2 — wire the tokeniser into the Terraform viewer

In `frontend/js/results.js`, function `renderTerraform` (search for
`function renderTerraform`), the `paintViewer` inner function currently does:

```js
code.append(el("span", { class: `viewer__line${isHit ? " is-hit" : ""}`, text: line || " " }));
```

Replace that single call with tokenised spans:

```js
import { findBlocks, tokeniseLine } from "./hcl.js";   // add at top of file

const lineEl = el("span", { class: `viewer__line${isHit ? " is-hit" : ""}` });
for (const token of tokeniseLine(line)) {
  lineEl.append(
    token.type === "plain"
      ? document.createTextNode(token.text)
      : el("span", { class: `tok tok--${token.type}`, text: token.text }),
  );
}
if (!line) lineEl.append(document.createTextNode(" "));
code.append(lineEl);
```

`tokeniseLine` returns `{type, text}` objects. Build them as elements — never
as an HTML string. Everything in the viewer originates in a user's prompt.

### Step 3 — add token colours to `frontend/styles/result.css`

Eight classes are needed, all resolving to tokens rather than literals:
`tok--keyword`, `tok--string`, `tok--number`, `tok--comment`, `tok--function`,
`tok--attr`, `tok--builtin`, `tok--constant`, `tok--punct`. Suggested mapping
against the existing palette:

```
keyword   var(--violet-500)     resource, data, variable, output
string    var(--green-600)      quoted values
number    var(--amber-600)      numeric literals
comment   var(--fg-faint)       italic
function  var(--blue-500)       merge(, jsonencode(
attr      var(--fg-default)     attribute names before =
builtin   var(--info)           var, local, path, each
constant  var(--amber-600)      true, false, null
punct     var(--fg-subtle)      braces, brackets, operators
```

Add a dark-theme override block for `keyword`, `string` and `function`; the
light values are too dark on the dark surface.

### Step 4 — folding and navigation

`findBlocks(text)` in `hcl.js` already returns
`{kind, type, name, start, end}` for every top-level block. Use it twice:

- a jump list beside the file list, rendering `type.name` and scrolling the
  viewer to `start` on click;
- a fold control in the gutter at each `start` line, hiding lines
  `start+1 .. end` when collapsed.

### Step 5 — verify before moving to Priority 3

```bash
python -m pytest                    # must stay at 580
node --check frontend/js/results.js
```

Then in the browser: generate anything, open the Terraform tab, confirm
highlighting appears, search still reports match counts, copy and download
still work, and the line numbers still align with the code after folding.

Folding must not break the gutter alignment — the gutter and the code are two
separate columns in a grid, so any line hidden in one must be hidden in the
other.
