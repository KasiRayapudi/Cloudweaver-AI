# Architecture

How the implementation maps onto the paper, and why each piece is built the way
it is.

## Paper section → code

| Paper | Stage | Module |
|---|---|---|
| VI, step 1 | User states the requirement | `frontend/`, `backend/cli.py` |
| VI, step 2 | NLP preprocessing and resource extraction | `app/nlp/rule_extractor.py`, `app/nlp/llm_extractor.py` |
| VI, step 3 | AI engine maps resources to cloud services | `app/engine/mapper.py`, `app/nlp/catalog.py` |
| VI, step 4 | Architecture diagram | `app/generators/diagram/` |
| VI, step 5 | Terraform IaC | `app/generators/terraform/` |
| VI, step 6 | Both presented to the user | `app/api/routes.py`, `app/export/bundle.py` |
| X (future work) | Security policy checks, cost hints | `app/engine/validator.py` |

## The shared representation

`app/models/ir.py` defines `InfrastructureSpec`: a list of `Resource` objects, a
list of typed `Edge` objects, and design-level facts (region, environment,
availability zones, high-availability flag).

Three rules keep it honest:

1. **Extractors report only what the user said.** They never add supporting
   plumbing. If they did, the rule and LLM paths would produce different
   architectures for the same prompt.
2. **The mapper adds everything else**, deterministically, for both paths.
3. **Generators are read-only over the finished spec.** Neither of them sees
   the prompt. This is what makes diagram/code consistency structural rather
   than something to be maintained by hand.

`Resource.origin` records whether each item was `explicit` (the user asked),
`implied` (something else required it) or `default` (baseline policy), which is
what the UI shades differently and what `spec.assumptions` explains in prose.

## Why a rule extractor at all

The paper's pipeline calls for AI-driven interpretation, and `llm_extractor.py`
provides it. But an LLM-only system has three properties that are bad for this
particular problem:

- output changes run to run, so the Terraform is not reproducible;
- it cannot run offline, in CI, or without an account;
- there is no ground truth to test the rest of the pipeline against.

The rule extractor fixes all three. It is longest-match phrase extraction over
a curated lexicon with span consumption, plus attribute regexes for counts,
instance sizes, engines, regions and environments. Its output is the oracle the
mapper, generators and 121 tests are written against. The LLM path is the
quality upgrade for phrasing the lexicon does not cover, constrained by a tool
schema so it can only speak the same vocabulary.

## Why hand-written HCL emission

`generators/terraform/hcl.py` is a small block model — `Block`, `HclFile`, `Raw`
— rather than string templates. String templates for a code generator go wrong
in predictable ways: escaping gets applied inconsistently, indentation drifts,
and tests end up asserting on whitespace. With a block model, values are escaped
in exactly one function, attribute alignment is computed rather than typed, and
tests can assert on structure.

`Raw` is the important distinction: `"aws_vpc.main.id"` is a string literal,
`Raw("aws_vpc.main.id")` is a reference. Getting that wrong is the single most
common bug in HCL generation, so the type system carries it.

## Why a custom diagram layout

Graphviz would have been the obvious choice and was rejected for two reasons.
It adds a native binary to the install story, and — more importantly — it does
not know that cloud diagrams are strongly layered. A general-purpose layout
optimises for edge crossings; a reader wants the internet at the top, the data
layer at the bottom, and the VPC drawn as a box around the middle.

`layout.py` therefore ranks nodes into fixed bands by tier, orders within each
band by barycentre against the band above (one pass converges for graphs this
shape), positions them centred, and routes edges as orthogonal polylines.
Security groups, IAM roles and alarms are pulled into a side column without
connectors: they attach to almost everything, and drawing those edges destroys
the diagram for no information gain. The full graph is still present in the
Mermaid export and the shared-model view.

## Validation without the Terraform binary

`terraform validate` needs the binary and a provider download. The test suite
instead parses the generated project, collects every `resource`/`data`
declaration, and asserts that every `aws_*.name` reference resolves to one of
them — plus the same check for `var.*`. That catches the real failure mode of a
generator (emitting a reference to something it forgot to create) in
milliseconds, offline. Running `terraform validate` against the output remains
worthwhile as a manual step; it is not a prerequisite for developing on this.

## Extending it

**A new AWS service:** add a `Kind`, one `ServiceInfo` row and one `LexEntry` in
`nlp/catalog.py`, then a branch in the relevant `_compute` / `_data` / `_edge`
method of the Terraform generator. The diagram picks it up automatically from
the catalog's tier and category.

**A new cloud provider:** add a catalog to `CATALOGS`, then a generator
alongside `terraform/generator.py`. The IR, extractors, mapper, validator and
diagram layer need no changes — that provider-neutrality is the reason the IR
uses names like `vm` and `object_storage` rather than `ec2` and `s3`.
