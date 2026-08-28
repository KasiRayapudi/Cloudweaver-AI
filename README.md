# AI-Driven Infrastructure Diagram and IaC Generator

Describe the cloud infrastructure you need in plain English. Get back an
architecture diagram **and** the Terraform that deploys it — both generated
from one shared model, so they cannot drift apart.

Reference implementation of the system described in *"AI-Driven Infrastructure
Diagram and Infrastructure as Code (IaC) Generator"* (S. Harsha Vardhan Reddy,
R. Kasi, Y. Sai Sandeep, P. Sai Tharun — Parul University).

---

## Why the outputs stay consistent

The problem the paper identifies is that teams draw the architecture in one
tool and write the IaC in another, and the two quietly diverge. This system
removes the second path entirely:

```
  natural language
        │
        ▼
  ┌───────────────┐   nlp/          rule-based extractor (default, offline)
  │  extraction   │                 or Claude with a constrained tool schema
  └───────┬───────┘
          ▼
  ┌───────────────┐   engine/mapper  implied VPC, subnets, gateways, security
  │   mapping     │                  groups, target groups, IAM roles, edges
  └───────┬───────┘
          ▼
  ╔═══════════════════════════╗
  ║  InfrastructureSpec (IR)  ║   ← the single source of truth
  ╚═══════╤═══════════╤═══════╝
          │           │
          ▼           ▼
   architecture     Terraform
     diagram         project
```

Neither generator ever reads the user's text. They read the same completed
resource graph, which is why the boxes on the diagram and the resources in the
HCL are the same set of things, always.

## The rule the generator is built around

> A resource may exist only if the user asked for it, or if something the user
> asked for cannot be deployed without it.

There is no third category. "Best practice", "production ready" and
"recommended" are not reasons to create infrastructure nobody asked for — they
are reasons to raise a **finding**, which the validator does.

Ask for one EC2 instance and you get eight resources, not thirty:

```
VPC · Public Subnet · Route Table · Internet Gateway
EC2 · Elastic IP · Security Group · IAM Role
```

No load balancer. No auto scaling group. No NAT gateway, RDS, Secrets Manager
or target group. Every resource carries the reason it exists:

```json
{ "resource": "EC2 Instance",  "origin": "explicit", "confidence": 0.90,
  "reason": "The requirement names 'ec2 instance'.", "source": "one ubuntu ec2 instance" }
{ "resource": "Public Subnet", "origin": "implied",  "confidence": 1.00,
  "reason": "EC2 Instance needs a subnet to launch into; public was used because no private subnet was requested." }
```

The rules live in [`engine/policy.py`](backend/app/engine/policy.py) as two
tables — `REQUIREMENTS` (mandatory dependencies) and `NON_DEPENDENCIES`
(services a human might add, recorded so the decision *not* to generate them is
explicit and testable). The mapper is a fixed-point loop over those tables with
no per-service knowledge of its own, which is what makes "never invent a
resource" a property of the engine rather than a promise about its control flow.

Two inferences are permitted, both narrowly: a load balancer and a scaling
group may appear for the phrases *high availability*, *auto scaling*, *web
tier*, or when more than one instance is requested. Each records the phrase
that triggered it.

### Placement rules

| Situation | Result |
|---|---|
| EC2, nothing said about placement | Public subnet + IGW + route table |
| "in a private subnet" | Private subnet + private route table + **NAT gateway** |
| Database, nothing said | Private subnets, **no NAT** — RDS needs no egress |
| Private subnet exists only for a database | Web server stays **public** |

That last row matters: a database creating private subnets for itself must not
drag public-facing compute in with it, which would in turn make a NAT gateway
look mandatory.

## Quick start

```bash
pip install -r requirements-dev.txt
```

(`requirements.txt` holds runtime dependencies only — it is what the deployed
function installs. `requirements-dev.txt` adds the test and lint tooling.)

Run the web app:

```bash
python -m uvicorn app.main:app --reload --app-dir backend
```

Then open http://127.0.0.1:8000.

Or use the CLI:

```bash
python backend/cli.py "a production web app in eu-west-1 with an auto scaling group behind a load balancer, a Multi-AZ PostgreSQL database and an S3 bucket" -o ./generated
```

## What you get

For a prompt like the one above:

```
generated/
├── terraform/
│   ├── versions.tf        provider + version constraints
│   ├── variables.tf       typed inputs with sensible defaults
│   ├── locals.tf          name prefix, tags, AZ and AMI lookups
│   ├── network.tf         VPC, per-AZ subnets, IGW, NAT, route tables
│   ├── security.tf        security groups chained tier to tier
│   ├── compute.tf         launch template, ASG, target tracking policy
│   ├── data.tf            RDS + subnet group, ElastiCache, S3 hardening
│   ├── edge.tf            ALB, target group, listener, CloudFront, DNS
│   ├── integration.tf     SQS/SNS, Secrets Manager, KMS
│   ├── iam.tf             roles, instance profile, least-privilege policy
│   ├── monitoring.tf      CloudWatch alarms
│   ├── outputs.tf         endpoints worth knowing after apply
│   ├── terraform.tfvars   values for this deployment
│   ├── user_data.sh       instance bootstrap
│   └── README.md          what was generated, and from which prompt
├── diagram/
│   ├── architecture.svg   self-contained, light/dark aware
│   └── architecture.mmd   Mermaid, for wikis and pull requests
└── spec.json              the shared model both outputs came from
```

## The two NLP backends

| | `rule` (default) | `llm` |
|---|---|---|
| Needs an API key | no | yes (`ANTHROPIC_API_KEY`) |
| Deterministic | yes | no |
| Handles unusual phrasing | limited to the lexicon | much better |
| Cost | none | per request |

The LLM backend is constrained to a tool schema built from the `Kind` enum, so
the model can only report resources the generators already understand — it
cannot hallucinate a service into your Terraform. If it is unavailable or the
call fails, the pipeline falls back to the rule extractor and says so in the
response.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export EXTRACTOR=llm
pip install anthropic
```

## Policy checks

Every generated design is validated before you see it — the "automated checks
against security policy" listed as future work in the paper:

- **error** — SSH/RDP open to `0.0.0.0/0`; a database in a public subnet; a
  public subnet with no internet gateway; duplicate CIDR blocks; circular
  dependencies
- **warning** — private compute with no NAT gateway (no outbound access);
  compute with no IAM role; a route table with no subnet; production without
  Multi-AZ; publicly readable buckets; unencrypted storage
- **info** — unused security groups, orphaned resources, oversized instances
  outside production, per-AZ NAT gateway cost

Because the engine no longer adds infrastructure to satisfy best practice, the
findings are where that advice now lives — visible next to the code, and yours
to accept or ignore.

`python backend/cli.py "..." --strict` exits non-zero on any error-severity
finding, which makes it usable as a CI gate.

## Layout

```
backend/
  app/
    models/ir.py            the shared representation
    nlp/                    catalog + lexicon, rule and LLM extractors
    engine/                 policy, mapper, validator, pipeline
    generators/terraform/   HCL writer + project generator
    generators/diagram/     layout engine, SVG renderer, Mermaid export
    api/                    FastAPI routes and schemas
    export/bundle.py        zip / on-disk packaging
  cli.py
  tests/                    367 tests
frontend/                   dependency-free single page UI
```

## Tests

```bash
python -m pytest
```

The `terraform` binary is not required. Generated projects are checked
structurally instead — balanced delimiters, and every `aws_*.name` reference
resolving to a resource the project actually declares, which is the failure
mode a code generator really has. `test_diagram.py` additionally asserts that
the diagram and the Terraform describe the same resource set.

## Deploying to Vercel

`vercel.json` and `api/index.py` are already set up. Import the repository at
[vercel.com/new](https://vercel.com/new) and deploy — no build settings to
change, no framework preset to pick.

How the request routing works:

| Path | Served by |
|---|---|
| `/api/*`, `/docs`, `/openapi.json` | the Python function (`api/index.py`) |
| `/static/*`, everything else | Vercel's CDN, straight from `frontend/` |

The function re-exports the same FastAPI app that `uvicorn` runs locally, so
there is no separate deployment code path to keep in sync. Static assets never
wake a function.

To enable the LLM extractor on the deployment, add two environment variables in
the Vercel project settings and redeploy:

```
ANTHROPIC_API_KEY = sk-ant-...
EXTRACTOR         = llm
```

`anthropic` is not in `requirements.txt`, so add it there too if you enable
this — otherwise the deployment stays on the rule extractor and says so in the
response, which is the intended fallback.

**One caveat worth knowing.** Serverless functions are stateless and time
limited. This app suits that well: generation is pure CPU, takes ~10 ms, and
holds nothing between requests. Expect a ~1 s cold start on the first hit.

## Current scope and limits

- **AWS only.** The IR and the catalog are provider-neutral by design;
  Azure and GCP need a new catalog entry per kind plus a matching generator.
- Generated Terraform is a **starting point, not an apply-and-forget artifact**.
  Read the plan. Nothing here has ever been applied to a real account.
- The rule extractor recognises the vocabulary in `nlp/catalog.py`. Phrasing
  well outside it is where the LLM backend earns its cost.
- No remote state backend is generated; add one before using this for anything
  shared.

## Roadmap

Taken from the paper's future work section, in the order they make sense:

1. Remote state + workspace scaffolding
2. Azure and GCP catalogs behind the same IR
3. Cost estimation from live pricing APIs rather than static hints
4. Kubernetes manifests alongside the cluster
5. CI/CD hook so a generated environment can go straight into a pipeline

## License

MIT.
