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

`Resource.origin` has exactly two values, and that is a deliberate constraint
rather than an omission: `explicit` (the user asked) and `implied` (a mandatory
dependency). There is no `default` or `recommended` origin, because there is no
code path that can produce one.

## The policy engine

`app/engine/policy.py` holds the entire ruleset as data:

- **`REQUIREMENTS`** maps each `Kind` to its mandatory dependencies. Each
  `Requirement` carries a `reason` shown to the user, an optional `when`
  condition, and an optional `id_hint`.
- **`NON_DEPENDENCIES`** records services a human architect might reasonably
  pair with a resource — a load balancer for an instance, a cache for a
  database — so that the decision *not* to generate them is written down and
  testable rather than an accident of control flow.

`ResourceMapper` is a fixed-point loop over that table. It adds mandatory
dependencies until the set stops growing, and it contains no per-service
knowledge, so a new service is an edit to the policy rather than a new branch
in the engine. This is what makes "never invent a resource" a property of the
system instead of a promise about its behaviour.

`id_hint` is the mechanism behind per-consumer resources. Without it, a design
gets one security group and one IAM role in total: a database would share the
application's firewall, and an ECS task would inherit the EC2 instance role —
which produced a dangling `aws_iam_role.task_role` reference in the generated
Terraform. With it, existence is checked by id, so each consumer gets exactly
one of its own and never two.

Two conditions carry most of the weight:

- `wants_private_placement` reads *stated intent*, not "a private subnet
  exists". A database creates private subnets for itself; if that counted as
  intent it would pull the public web server in with it, which would in turn
  make a NAT gateway look mandatory. One true fact would cascade into three
  wrong resources.
- `has_private_compute` runs during closure, *before* subnet placement has been
  recorded on each resource, so it reads intent rather than the `subnet_band`
  property that does not exist yet.

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
mapper, generators and 494 tests are written against. The LLM path is the
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

## Three layers of validation

Each layer catches something the others cannot, which is why all three exist.

**1. Structural, in the unit tests.** The suite parses the generated project,
collects every `resource`/`data` declaration, and asserts that every
`aws_*.name` reference resolves to one of them, plus the same check for
`var.*`. This catches the real failure mode of a generator — emitting a
reference to something it forgot to create — in milliseconds and offline.

**2. `terraform validate`, in `scripts/tf_validate.py`.** Twelve
structurally distinct projects are generated and validated against the real
binary and the real AWS provider schema. This is the only layer that knows
whether an argument actually exists on a resource. It found a live defect on
its first run: an Elastic IP referencing a counted instance without an index.

One implementation note that matters for CI: Terraform copies the provider
into every working directory it initialises, roughly 600 MB apiece, and the
plugin cache cannot symlink on Windows. Initialising a directory per project
filled a 200 GB disk within twelve projects. The harness therefore initialises
*one* directory and swaps each project's files into it.

**3. AWS API constraints, in `engine/constraints.py`.** `terraform validate`
checks schema, not provider semantics. An `aws_db_instance` carrying an Aurora
engine and a 42-character load balancer name both validate cleanly and are
then rejected by the AWS API at apply. This layer covers exactly that gap, and
is why the Aurora and name-length defects could be found without an AWS
account.

**Plus an emission audit.** `engine/emission.py` compares the finished
Terraform back against the model and reports any resource the generator
dropped. The generator still assumes one resource per kind in places; the
audit does not remove that limitation, it makes hitting it impossible to miss.

`test_coverage.py` adds the two checks that matter most for a generator built
around a shared model, run for all 34 kinds:

- **Forward** — every resource in the model must emit its catalog
  `terraform_type`. A resource the diagram draws and the code never creates is
  the exact drift this project exists to prevent. This check found four: route
  tables, Elastic IPs, Redshift (in the catalog, priced in the cost estimate,
  drawn on the diagram, and never generated at all) and monitoring.
- **Reverse** — every emitted `resource` block must map back to the model or to
  a declared allowlist of implementation details (subnet groups, listeners,
  policy attachments, generated passwords). This found an SNS alerts topic that
  appeared in the Terraform whenever monitoring was requested but existed
  nowhere in the model.

## Extending it

**A new AWS service:** add a `Kind`, one `ServiceInfo` row and one `LexEntry` in
`nlp/catalog.py`, then a branch in the relevant `_compute` / `_data` / `_edge`
method of the Terraform generator. The diagram picks it up automatically from
the catalog's tier and category.

**A new cloud provider:** add a catalog to `CATALOGS`, then a generator
alongside `terraform/generator.py`. The IR, extractors, mapper, validator and
diagram layer need no changes — that provider-neutrality is the reason the IR
uses names like `vm` and `object_storage` rather than `ec2` and `s3`.
