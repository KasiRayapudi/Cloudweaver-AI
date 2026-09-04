"""The AI Cloud Architect: questions answered from the generated design.

This is not a chatbot. Every answer it can give deterministically, it gives
deterministically, from data the pipeline already produced:

    InfrastructureSpec -> Decision Trace -> Explanations
                       -> Optimizer -> Validator -> Cost

A language model is consulted only when a question is genuinely outside that
data — general AWS concepts, learning questions — and the answer says so.

Three properties are structural rather than promised:

* **Read-only.** ``ask`` receives a finished :class:`GenerationResult` and
  returns an :class:`Answer`. This module imports no mapper, no generator and
  no extractor, so it has nothing to mutate even by accident.
* **Deterministic routing.** Intent is decided by pattern, not by a model. The
  same question routes the same way every time, which is what lets the rest of
  the guarantees hold.
* **Sourced.** Every answer records which engines produced it. A reader can
  always tell whether they are looking at a fact about their design or a
  model's general knowledge.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum

from app.engine.explain import ALTERNATIVES
from app.models.ir import InfrastructureSpec, Kind, Origin
from app.nlp.catalog import LEXICON, service_for


class Intent(str, Enum):
    """What kind of question this is. Decided by pattern, never by a model."""

    EXPLANATION = "explanation"
    OPTIMIZATION = "optimization"
    COMPARISON = "comparison"
    VALIDATION = "validation"
    COST = "cost"
    TERRAFORM = "terraform"
    NETWORKING = "networking"
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    DEPLOYMENT = "deployment"
    UNKNOWN = "unknown"


class Source(str, Enum):
    """Which engine produced an answer. Shown to the user verbatim."""

    SPEC = "InfrastructureSpec"
    TRACE = "Decision Trace"
    EXPLAIN = "Explainability Layer"
    OPTIMIZER = "Optimization Engine"
    VALIDATOR = "Validation Engine"
    COST = "Cost Estimator"
    TERRAFORM = "Terraform Generator"
    LLM = "LLM (general AWS knowledge)"


@dataclass
class Answer:
    """One reply, with everything the interface needs to present it honestly."""

    question: str
    intent: str
    text: str
    sources: list[str] = field(default_factory=list)
    deterministic: bool = True
    confidence: float = 1.0
    #: Resource ids mentioned, so the interface can link them to the inspector.
    resources: list[str] = field(default_factory=list)
    #: Full recommendation dicts, so a suggestion arrives with its priority,
    #: saving and difficulty rather than as prose the user has to trust.
    recommendations: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    code: str | None = None
    follow_ups: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Intent patterns. Ordered: the first match wins, so specific forms are
# listed before the general ones they would otherwise be swallowed by.
# --------------------------------------------------------------------------

_PATTERNS: tuple[tuple[re.Pattern[str], Intent], ...] = (
    # Comparison must precede explanation: "compare Aurora vs RDS" contains
    # neither "why" nor "explain" but does contain service names.
    (re.compile(r"\b(compare|versus|vs\.?|difference between|instead of)\b"), Intent.COMPARISON),
    (re.compile(r"\balternative"), Intent.COMPARISON),

    # "Why is X here?" is a provenance question whatever X is, so it must be
    # matched before the topical patterns below. Without this, "why was the
    # NAT gateway added?" is caught by the networking pattern -- which does
    # answer it, but by a route that cannot cite the policy rule that added
    # it, which is the only interesting part of the answer.
    (re.compile(r"\bwhy\b.{0,60}\b(added|create[ds]?|generated|included|here|"
                r"exists?|needed|needs?|required|requires?)\b"), Intent.EXPLANATION),

    (re.compile(r"\b(reduce|lower|cut|save|cheaper|optimi[sz]e).{0,20}\bcost"),
     Intent.OPTIMIZATION),
    (re.compile(r"\bimprove\b|\bsuggest|\bbetter\b|\brecommend|\bharden\b"), Intent.OPTIMIZATION),
    (re.compile(r"\bproduction[- ]ready\b|\bmake it (highly available|resilient)"),
     Intent.OPTIMIZATION),

    # Plurals matter: a trailing \b after "error" excludes "errors", which is
    # how most people actually phrase the question.
    (re.compile(r"\b(validation|finding|error|warning|issue|problem)s?\b|\bwrong\b"),
     Intent.VALIDATION),

    (re.compile(r"\b(cost|price|pricing|spend|bill|expensive|budget)\b"), Intent.COST),

    (re.compile(r"\b(terraform|hcl|code|snippet|\.tf\b|resource block)\b"), Intent.TERRAFORM),

    (re.compile(r"\b(network|networking|subnet|vpc|routing|route table|cidr|nat|"
                r"internet gateway|igw|private|public)\b"), Intent.NETWORKING),

    (re.compile(r"\b(security group|iam|role|permission|encrypt|secret|tls|https|"
                r"certificate|firewall|waf|secure|security)\b"), Intent.SECURITY),

    (re.compile(r"\b(deploy|apply|plan|init|rollout|release|checklist)\b"), Intent.DEPLOYMENT),

    (re.compile(r"^\s*why\b|\bwhy (was|is|are|does|do|did)\b|\bwhat (policy|rule|triggered)\b"),
     Intent.EXPLANATION),

    # "explain this architecture" is the commonest phrasing and required
    # "the", so it fell through to the generic explanation branch and found
    # no resource called "architecture".
    (re.compile(r"\b(explain|describe|walk me through)\s+"
                r"(the |this |my |our |your )?"
                r"(architecture|design|infrastructure|setup|whole thing)\b|"
                r"\b(overview|summar)"), Intent.ARCHITECTURE),
    (re.compile(r"\bexplain\b|\bwhat is\b|\bwhat does\b|\bhow does\b|\bshow me\b|\bshow\b"),
     Intent.EXPLANATION),
)

#: Questions the deterministic layer cannot answer because they are not about
#: this design at all. These go to the LLM when one is configured.
_GENERAL_KNOWLEDGE = re.compile(
    r"\bwhat is (aws|amazon|terraform|a vpc|an? \w+ service)\b|"
    r"\bhow (do|does) (aws|terraform|kubernetes)\b|"
    r"\bbest practice(s)? for\b|\blearn\b|\btutorial\b|\bin general\b",
)


def classify(question: str) -> Intent:
    """Map a question to the module that should answer it."""
    text = question.lower().strip()
    if not text:
        return Intent.UNKNOWN
    for pattern, intent in _PATTERNS:
        if pattern.search(text):
            return intent
    return Intent.UNKNOWN


# --------------------------------------------------------------------------
# Comparisons. Written as trade-offs, because "X is better" is never true
# without saying for what.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Comparison:
    left: str
    right: str
    summary: str
    rows: tuple[tuple[str, str, str], ...]   # (dimension, left, right)
    choose_left: str
    choose_right: str


COMPARISONS: tuple[Comparison, ...] = (
    Comparison(
        "EC2", "ECS Fargate",
        "Virtual machines you operate, against containers AWS operates for you.",
        (
            ("Unit of deployment", "An AMI on an instance", "A container image in a task"),
            ("You patch", "The OS and the runtime", "The image only"),
            ("Scaling", "Add instances, minutes", "Add tasks, seconds"),
            ("Cost shape", "Cheaper per vCPU-hour", "Higher per vCPU-hour, no idle capacity"),
            ("State", "Local disk survives a reboot", "Task storage is ephemeral"),
        ),
        "The workload needs a specific kernel, a licence tied to a host, "
        "GPUs, or persistent local disk.",
        "The workload is already containerised and you would rather spend the "
        "time on the application than on the fleet.",
    ),
    Comparison(
        "ECS", "EKS",
        "Two container orchestrators: one AWS-shaped and simple, one "
        "Kubernetes and portable.",
        (
            ("Control plane cost", "None", "About $73 a month per cluster"),
            ("API", "AWS-specific", "Kubernetes, portable across clouds"),
            ("Operational load", "Low", "Real — upgrades, add-ons, version skew"),
            ("Ecosystem", "AWS services", "The whole CNCF landscape"),
            ("Team requirement", "AWS familiarity", "Kubernetes expertise"),
        ),
        "You are on AWS, want containers, and have no specific need for the "
        "Kubernetes API. Most teams that pick EKS by default should be here.",
        "You need Kubernetes itself: portability, an operator you depend on, "
        "or a team that already runs it.",
    ),
    Comparison(
        "RDS", "Aurora",
        "The same engines, two storage architectures.",
        (
            ("Storage", "An EBS volume you size", "Distributed, grows automatically"),
            ("Failover", "60–120 seconds", "Typically under 30 seconds"),
            ("Read scaling", "Up to 5 replicas, replication lag", "Up to 15 readers, lower lag"),
            ("Cost at rest", "Lower", "Roughly 20–30% higher"),
            ("Terraform", "aws_db_instance", "aws_rds_cluster plus instances"),
        ),
        "The workload is modest, cost matters more than failover speed, and "
        "one instance with a standby is enough.",
        "You need faster failover, readers that scale independently, or "
        "storage that grows without a maintenance window.",
    ),
    Comparison(
        "Lambda", "ECS Fargate",
        "Per-request compute against always-warm containers.",
        (
            ("Billing", "Per millisecond of execution", "Per second a task runs"),
            ("Idle cost", "None", "The task keeps running"),
            ("Maximum duration", "15 minutes", "Unbounded"),
            ("Cold start", "Real, milliseconds to seconds", "None once running"),
            ("Concurrency", "Automatic to the account limit", "You set the task count"),
        ),
        "Work is event-driven, bursty, and each unit finishes quickly.",
        "Work is continuous, long-running, or needs persistent connections.",
    ),
    Comparison(
        "NAT Gateway", "VPC Endpoints",
        "Two ways for private subnets to reach services outside them.",
        (
            ("Reaches", "Anything on the internet", "Specific AWS services only"),
            ("Cost", "Hourly plus per GB", "Gateway endpoints are free"),
            ("Availability", "Zonal — one per AZ for resilience", "Regional"),
            ("Setup", "One resource plus routes", "One endpoint per service"),
        ),
        "Private workloads need general internet access — package installs, "
        "third-party APIs, webhooks.",
        "Traffic is only to S3 or DynamoDB. A gateway endpoint removes both "
        "the hourly charge and the per-gigabyte fee for it.",
    ),
    Comparison(
        "Application Load Balancer", "Network Load Balancer",
        "Layer 7 routing against layer 4 throughput.",
        (
            ("Layer", "7 — HTTP aware", "4 — TCP and TLS"),
            ("Routing", "By path, host and header", "By port only"),
            ("Client IP", "In X-Forwarded-For", "Preserved on the connection"),
            ("Static IP", "No, use the DNS name", "Yes, one per zone"),
            ("Latency", "Low", "Lower"),
        ),
        "Traffic is HTTP and you want path-based routing, header inspection "
        "or a WAF in front.",
        "You need static addresses, extreme throughput, or a protocol that is "
        "not HTTP.",
    ),
)


def _find_comparison(question: str) -> Comparison | None:
    """Match a question to a comparison, in either direction."""
    text = question.lower()
    aliases = {
        "ec2": "EC2", "instance": "EC2", "virtual machine": "EC2",
        "ecs": "ECS", "fargate": "ECS Fargate", "container service": "ECS",
        "eks": "EKS", "kubernetes": "EKS", "k8s": "EKS",
        "rds": "RDS", "aurora": "Aurora",
        "lambda": "Lambda", "serverless": "Lambda", "function": "Lambda",
        "nat": "NAT Gateway", "endpoint": "VPC Endpoints",
        "alb": "Application Load Balancer",
        "application load balancer": "Application Load Balancer",
        "nlb": "Network Load Balancer", "network load balancer": "Network Load Balancer",
    }
    mentioned = {
        canonical for alias, canonical in aliases.items()
        if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text)
    }

    best: Comparison | None = None
    best_score = 0
    for comparison in COMPARISONS:
        score = 0
        for side in (comparison.left, comparison.right):
            if any(side.startswith(name) or name.startswith(side) for name in mentioned):
                score += 1
        if score > best_score:
            best, best_score = comparison, score
    return best if best_score >= 2 else None


# --------------------------------------------------------------------------
# Resource resolution, reusing the extractor's own vocabulary.
# --------------------------------------------------------------------------

def _kinds_in(question: str) -> list[Kind]:
    """Kinds the question names, using the catalog lexicon.

    Deliberately the same vocabulary the extractor uses. A second table of
    service synonyms would drift from the first, and then the assistant would
    disagree with the design about what a word means.
    """
    text = question.lower()
    found: list[Kind] = []
    for entry in LEXICON:
        for phrase in sorted(entry.phrases, key=len, reverse=True):
            if re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?:e?s)?(?![a-z0-9])", text):
                if entry.kind not in found:
                    found.append(entry.kind)
                break
    return found


def _resources_in(question: str, spec: InfrastructureSpec) -> list:
    """Resources the question refers to, by id first, then by service name."""
    text = question.lower()

    by_id = [r for r in spec.resources if re.search(
        r"(?<![a-z0-9_])" + re.escape(r.id.lower()) + r"(?![a-z0-9_])", text)]
    if by_id:
        return by_id

    kinds = _kinds_in(question)
    return [r for r in spec.resources if r.kind in kinds]


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"• {line}" for line in lines)


class Architect:
    """Answers questions about a generated design.

    Holds no state and no reference to any component that could change the
    design. Every method receives the finished result and returns prose.
    """

    def __init__(self, llm=None) -> None:
        #: Optional. When absent every question is still answered, or honestly
        #: declined — the assistant must never fail because a model is missing.
        self.llm = llm

    # ------------------------------------------------------------------

    def ask(self, question: str, result) -> Answer:
        question = (question or "").strip()
        if not question:
            return Answer(
                question="", intent=Intent.UNKNOWN.value,
                text="Ask something about this architecture — why a resource "
                     "is there, how to reduce its cost, or how two services "
                     "compare.",
                sources=[], follow_ups=self.suggestions(result),
            )

        intent = classify(question)
        handler = {
            Intent.EXPLANATION: self._explain,
            Intent.OPTIMIZATION: self._optimize,
            Intent.COMPARISON: self._compare,
            Intent.VALIDATION: self._validate,
            Intent.COST: self._cost,
            Intent.TERRAFORM: self._terraform,
            Intent.NETWORKING: self._networking,
            Intent.SECURITY: self._security,
            Intent.ARCHITECTURE: self._architecture,
            Intent.DEPLOYMENT: self._deployment,
        }.get(intent)

        answer = handler(question, result) if handler else None
        if answer is not None:
            answer.follow_ups = answer.follow_ups or self.suggestions(result)
            return answer

        return self._fallback(question, result, intent)

    # ------------------------------------------------------------------
    # explanation
    # ------------------------------------------------------------------

    def _explain(self, question: str, result) -> Answer | None:
        spec = result.spec
        explanations = {e.resource_id: e for e in result.explanations}

        # "why wasn't X added?" — answered from the recorded exclusions and
        # from the policy's own refusal to invent anything.
        if re.search(r"\bwhy (was)?n[o']?t\b|\bwhy no\b|\bwhy didn'?t\b|\bwhy is there no\b",
                     question.lower()):
            return self._explain_absence(question, result)

        targets = _resources_in(question, spec)
        if not targets:
            return None

        blocks: list[str] = []
        for resource in targets[:3]:
            explanation = explanations.get(resource.id)
            if explanation is None:
                continue
            lines = [f"**{explanation.name}** (`{explanation.resource_id}`)", ""]

            if explanation.requested:
                lines.append(f"You asked for it. {explanation.reason}")
                if explanation.evidence:
                    lines.append(f"The words that matched: “{explanation.evidence}”")
            else:
                lines.append(f"It was added as a mandatory dependency. {explanation.reason}")
                if explanation.rule:
                    lines.append(f"Policy rule: `{explanation.rule}`")
                if explanation.triggered_by:
                    trigger = spec.get(explanation.triggered_by)
                    name = trigger.name if trigger else explanation.triggered_by
                    lines.append(
                        f"It exists because you asked for **{name}**, which "
                        "cannot be deployed without it."
                    )

            if explanation.depends_on:
                lines.append("")
                lines.append("Created after: " + ", ".join(
                    f"`{i}`" for i in explanation.depends_on))
            if explanation.required_by:
                lines.append("Required by: " + ", ".join(
                    f"`{i}`" for i in explanation.required_by))
            if explanation.monthly_cost_usd:
                lines.append(f"Estimated cost: ${explanation.monthly_cost_usd:.2f} a month.")
            if explanation.networking_notes:
                lines.append("")
                lines.append(explanation.networking_notes)
            elif explanation.security_notes:
                lines.append("")
                lines.append(explanation.security_notes)

            blocks.append("\n".join(lines))

        if not blocks:
            return None

        return Answer(
            question=question,
            intent=Intent.EXPLANATION.value,
            text="\n\n---\n\n".join(blocks),
            sources=[Source.SPEC.value, Source.TRACE.value, Source.EXPLAIN.value],
            resources=[r.id for r in targets[:3]],
            confidence=1.0,
        )

    def _explain_absence(self, question: str, result) -> Answer:
        """Why something is *not* in the design."""
        spec = result.spec
        kinds = _kinds_in(question)

        for exclusion in spec.exclusions:
            if exclusion.kind in kinds:
                return Answer(
                    question=question,
                    intent=Intent.EXPLANATION.value,
                    text=(
                        f"It was deliberately excluded. Your requirement said "
                        f"“{exclusion.cue}” before “{exclusion.phrase}”, which "
                        "this system reads as a refusal rather than a request.\n\n"
                        f"The words involved: “{exclusion.evidence}”\n\n"
                        "Remove that phrasing and it will be generated."
                    ),
                    sources=[Source.SPEC.value, Source.TRACE.value],
                )

        if kinds:
            present = [k for k in kinds if spec.has(k)]
            if present:
                return Answer(
                    question=question,
                    intent=Intent.EXPLANATION.value,
                    text="It is in the design — "
                         + ", ".join(f"`{r.id}`" for r in spec.of_kind(*present))
                         + ". Ask why it was added to see the rule behind it.",
                    sources=[Source.SPEC.value],
                    resources=[r.id for r in spec.of_kind(*present)],
                )

            name = service_for(kinds[0]).display
            return Answer(
                question=question,
                intent=Intent.EXPLANATION.value,
                text=(
                    f"**{name}** is not in this design because you did not ask "
                    "for it, and nothing you did ask for requires it.\n\n"
                    "This system generates two things only: resources you named, "
                    "and the mandatory dependencies of those resources. It never "
                    "adds anything because it is generally a good idea — that is "
                    "what the Optimise tab is for, where a suggestion stays a "
                    "suggestion.\n\n"
                    f"To include it, name it in the requirement."
                ),
                sources=[Source.SPEC.value, Source.TRACE.value],
            )

        return Answer(
            question=question,
            intent=Intent.EXPLANATION.value,
            text=(
                "Nothing is generated unless you asked for it or something you "
                "asked for requires it. If a service is missing, it is because "
                "neither was true — name it in the requirement to include it."
            ),
            sources=[Source.SPEC.value],
        )

    # ------------------------------------------------------------------
    # optimisation
    # ------------------------------------------------------------------

    _CATEGORY_WORDS = {
        "cost": "cost", "cheap": "cost", "save": "cost", "spend": "cost",
        "secur": "security", "harden": "security",
        "reliab": "reliability", "available": "reliability", "resilien": "reliability",
        "perform": "performance", "fast": "performance", "latency": "performance",
        "network": "networking",
        "complian": "compliance",
        "operat": "operations", "monitor": "operations",
    }

    def _optimize(self, question: str, result) -> Answer:
        text = question.lower()
        wanted = {
            category for word, category in self._CATEGORY_WORDS.items()
            if word in text
        }

        picked = [
            r for r in result.recommendations
            if not wanted or r.category in wanted
        ]
        if not picked:
            picked = list(result.recommendations)

        if not picked:
            return Answer(
                question=question,
                intent=Intent.OPTIMIZATION.value,
                text="Every optimisation rule passed against this design, so "
                     "there is nothing to suggest. That is a statement about "
                     "the rules as much as the architecture — the optimiser "
                     "only reports what it can check.",
                sources=[Source.OPTIMIZER.value],
            )

        saving = sum(r.monthly_delta_usd for r in picked if r.monthly_delta_usd < 0)
        spend = sum(r.monthly_delta_usd for r in picked if r.monthly_delta_usd > 0)

        heading = (
            f"{len(picked)} suggestion{'s' if len(picked) != 1 else ''}"
            + (f" for {', '.join(sorted(wanted))}" if wanted else "")
            + ", highest priority first."
        )

        lines = [heading, ""]
        for item in picked[:6]:
            delta = (
                "no cost" if item.monthly_delta_usd == 0
                else f"saves ${abs(item.monthly_delta_usd):.2f}/mo" if item.monthly_delta_usd < 0
                else f"adds ${item.monthly_delta_usd:.2f}/mo"
            )
            lines.append(
                f"**{item.title}** — {item.priority}, {delta}, {item.difficulty}\n"
                f"{item.reason}\n"
                f"→ {item.action}"
            )
            lines.append("")

        if len(picked) > 6:
            lines.append(f"…and {len(picked) - 6} more in the Optimise tab.")
            lines.append("")

        if saving:
            lines.append(f"Adopting every saving here would remove about "
                         f"${abs(saving):.2f} a month.")
        if spend:
            lines.append(f"Adopting every addition would add about "
                         f"${spend:.2f} a month.")
        lines.append("")
        lines.append("None of this has been applied. Recommendations stay "
                     "advisory — the generated Terraform still contains only "
                     "what you asked for and its mandatory dependencies.")

        return Answer(
            question=question,
            intent=Intent.OPTIMIZATION.value,
            text="\n".join(lines),
            sources=[Source.OPTIMIZER.value, Source.SPEC.value, Source.COST.value],
            resources=sorted({r for item in picked[:6] for r in item.resources}),
            recommendations=[item.to_dict() for item in picked],
        )

    # ------------------------------------------------------------------
    # comparison
    # ------------------------------------------------------------------

    def _compare(self, question: str, result) -> Answer:
        comparison = _find_comparison(question)
        if comparison is not None:
            rows = "\n".join(
                f"• **{dimension}** — {comparison.left}: {left} · "
                f"{comparison.right}: {right}"
                for dimension, left, right in comparison.rows
            )
            text = (
                f"**{comparison.left} vs {comparison.right}**\n\n"
                f"{comparison.summary}\n\n"
                f"{rows}\n\n"
                f"**Choose {comparison.left}** when {comparison.choose_left}\n\n"
                f"**Choose {comparison.right}** when {comparison.choose_right}"
            )

            # Ground the comparison in this design where it applies.
            present = [
                r for r in result.spec.resources
                if service_for(r.kind).display.lower().startswith(
                    comparison.left.split()[0].lower())
                or service_for(r.kind).display.lower().startswith(
                    comparison.right.split()[0].lower())
            ]
            if present:
                text += "\n\nIn this design you are using: " + ", ".join(
                    f"**{r.name}** (`{r.id}`)" for r in present)

            return Answer(
                question=question,
                intent=Intent.COMPARISON.value,
                text=text,
                sources=[Source.EXPLAIN.value] + ([Source.SPEC.value] if present else []),
                resources=[r.id for r in present],
            )

        # No named pair: fall back to the alternatives recorded for whatever
        # resources the question mentions.
        targets = _resources_in(question, result.spec)
        blocks = []
        for resource in targets[:2]:
            options = ALTERNATIVES.get(resource.kind, [])
            if not options:
                continue
            body = "\n".join(
                f"• **{option['service']}** — {option['when']}" for option in options
            )
            blocks.append(f"Instead of **{resource.name}**:\n\n{body}")

        if blocks:
            return Answer(
                question=question,
                intent=Intent.COMPARISON.value,
                text="\n\n".join(blocks),
                sources=[Source.EXPLAIN.value, Source.SPEC.value],
                resources=[r.id for r in targets[:2]],
            )

        available = ", ".join(f"{c.left} vs {c.right}" for c in COMPARISONS)
        return Answer(
            question=question,
            intent=Intent.COMPARISON.value,
            text=f"I can compare these directly: {available}.\n\n"
                 "Or ask about a resource in this design and I will list the "
                 "alternatives recorded for it.",
            sources=[Source.EXPLAIN.value],
        )

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------

    def _validate(self, question: str, result) -> Answer:
        findings = result.findings
        if not findings:
            return Answer(
                question=question,
                intent=Intent.VALIDATION.value,
                text="No findings. This design passes every structural, "
                     "network, security, reliability and AWS deployment check "
                     "the validator runs.",
                sources=[Source.VALIDATOR.value],
            )

        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        infos = [f for f in findings if f.severity == "info"]

        lines = []
        if errors:
            lines.append(
                f"**{len(errors)} error{'s' if len(errors) != 1 else ''}** — these "
                "would fail during `terraform apply`:")
            lines.extend(f"• {f.message}" for f in errors)
            lines.append("")
        if warnings:
            lines.append(
                f"**{len(warnings)} warning{'s' if len(warnings) != 1 else ''}** — "
                "these deploy, but cost you money, security or availability:")
            lines.extend(f"• {f.message}" for f in warnings)
            lines.append("")
        if infos:
            lines.append(f"**{len(infos)} recommendation"
                         f"{'s' if len(infos) != 1 else ''}**:")
            lines.extend(f"• {f.message}" for f in infos)
            lines.append("")

        lines.append(
            "Nothing blocks deployment." if not errors
            else "Fix the errors before running apply.")

        return Answer(
            question=question,
            intent=Intent.VALIDATION.value,
            text="\n".join(lines),
            sources=[Source.VALIDATOR.value],
            findings=[f.to_dict() for f in findings],
            resources=[f.resource_id for f in findings if f.resource_id],
        )

    # ------------------------------------------------------------------
    # cost
    # ------------------------------------------------------------------

    def _cost(self, question: str, result) -> Answer:
        total = result.estimated_monthly_cost
        priced = sorted(
            [e for e in result.explanations if e.monthly_cost_usd > 0],
            key=lambda e: e.monthly_cost_usd,
            reverse=True,
        )

        if not priced:
            return Answer(
                question=question,
                intent=Intent.COST.value,
                text="Nothing in this design carries a fixed monthly charge. "
                     "The services in it are billed on what you actually use.",
                sources=[Source.COST.value],
            )

        lines = [
            f"About **${total:.2f} a month**, ${total / 30.44:.2f} a day, "
            f"${total * 12:,.0f} a year.",
            "",
            "Largest first:",
        ]
        lines.extend(
            f"• **{e.name}** (`{e.resource_id}`) — ${e.monthly_cost_usd:.2f}"
            for e in priced[:6]
        )

        savings = [r for r in result.recommendations if r.monthly_delta_usd < 0]
        if savings:
            total_saving = abs(sum(r.monthly_delta_usd for r in savings))
            lines.append("")
            lines.append(
                f"There {'is' if len(savings) == 1 else 'are'} "
                f"{len(savings)} cost suggestion{'s' if len(savings) != 1 else ''} "
                f"worth about ${total_saving:.2f} a month — ask me to reduce cost."
            )

        lines.append("")
        lines.append(
            "These are static per-service rates, not live AWS pricing, and "
            "they exclude data transfer, requests and storage growth. Use them "
            "to compare designs, not to forecast a bill."
        )

        return Answer(
            question=question,
            intent=Intent.COST.value,
            text="\n".join(lines),
            sources=[Source.COST.value, Source.SPEC.value]
                    + ([Source.OPTIMIZER.value] if savings else []),
            resources=[e.resource_id for e in priced[:6]],
            recommendations=[r.to_dict() for r in savings],
        )

    # ------------------------------------------------------------------
    # terraform
    # ------------------------------------------------------------------

    def _terraform(self, question: str, result) -> Answer:
        targets = _resources_in(question, result.spec)
        explanations = {e.resource_id: e for e in result.explanations}

        for resource in targets:
            explanation = explanations.get(resource.id)
            if explanation and explanation.terraform_snippet:
                return Answer(
                    question=question,
                    intent=Intent.TERRAFORM.value,
                    text=f"**{resource.name}** is generated in "
                         f"`{explanation.terraform_file}`:",
                    code=explanation.terraform_snippet,
                    sources=[Source.TERRAFORM.value, Source.EXPLAIN.value],
                    resources=[resource.id],
                )

        files = ", ".join(f"`{name}`" for name in sorted(result.terraform) if name.endswith(".tf"))
        return Answer(
            question=question,
            intent=Intent.TERRAFORM.value,
            text=f"The project has {len(result.terraform)} files: {files}.\n\n"
                 "Name a resource and I will show the block that creates it.",
            sources=[Source.TERRAFORM.value],
        )

    # ------------------------------------------------------------------
    # networking
    # ------------------------------------------------------------------

    def _networking(self, question: str, result) -> Answer | None:
        spec = result.spec

        # A "why" question about a specific resource is an explanation that
        # happens to mention networking words.
        if re.search(r"\bwhy\b", question.lower()):
            explained = self._explain(question, result)
            if explained is not None:
                return explained

        if not spec.has(Kind.VPC):
            return Answer(
                question=question,
                intent=Intent.NETWORKING.value,
                text="This design has no VPC. Nothing in it is placed in a "
                     "network you control — the services used are all regional "
                     "endpoints.",
                sources=[Source.SPEC.value],
            )

        vpc = spec.first(Kind.VPC)
        public = spec.of_kind(Kind.SUBNET_PUBLIC)
        private = spec.of_kind(Kind.SUBNET_PRIVATE)
        nat = spec.of_kind(Kind.NAT_GATEWAY)
        igw = spec.of_kind(Kind.INTERNET_GATEWAY)

        lines = [
            f"**{vpc.name}**"
            + (f" — existing, `{vpc.external_id}`" if vpc.is_external
               else f" — `{vpc.properties.get('cidr_block', '10.0.0.0/16')}`"),
            f"Spread across **{spec.availability_zones} availability zone"
            f"{'s' if spec.availability_zones != 1 else ''}** in {spec.region}.",
            "",
        ]

        if public:
            lines.append(
                "**Public subnets** have a route to the internet gateway. "
                "That single route is the only thing that makes a subnet public."
            )
        if private:
            lines.append(
                "**Private subnets** have no route to the internet gateway, so "
                "nothing in them can be reached from outside the VPC."
            )
        if nat:
            lines.append(
                f"**{len(nat)} NAT gateway{'s' if len(nat) != 1 else ''}** let "
                "private workloads make outbound connections while accepting "
                "none inbound."
            )
        elif private:
            lines.append(
                "There is no NAT gateway, so workloads in private subnets have "
                "no outbound internet access at all."
            )
        if igw:
            lines.append(
                "The **internet gateway** is the VPC's only path to and from "
                "the internet."
            )

        placed = [
            (r, r.properties.get("subnet_band"))
            for r in spec.resources
            if r.properties.get("subnet_band")
        ]
        if placed:
            lines.append("")
            lines.append("Placement:")
            lines.extend(
                f"• **{r.name}** — {band} subnets" for r, band in placed
            )

        return Answer(
            question=question,
            intent=Intent.NETWORKING.value,
            text="\n".join(lines),
            sources=[Source.SPEC.value, Source.EXPLAIN.value],
            resources=[r.id for r in ([vpc] + public + private + nat + igw)],
        )

    # ------------------------------------------------------------------
    # security
    # ------------------------------------------------------------------

    def _security(self, question: str, result) -> Answer | None:
        spec = result.spec

        if re.search(r"\bwhy\b", question.lower()):
            explained = self._explain(question, result)
            if explained is not None:
                return explained

        groups = spec.of_kind(Kind.SECURITY_GROUP)
        roles = spec.of_kind(Kind.IAM_ROLE)
        lines: list[str] = []

        if groups:
            lines.append(
                f"**{len(groups)} security group"
                f"{'s' if len(groups) != 1 else ''}**, one per tier. A group is "
                "a stateful allow-list: return traffic for an allowed "
                "connection is permitted automatically."
            )
            for group in groups:
                ports = group.properties.get("ingress_ports", [])
                source = group.properties.get("ingress_from", "the VPC")
                lines.append(
                    f"• **{group.name}** (`{group.id}`) — "
                    + (f"ports {', '.join(str(p) for p in ports)}" if ports else "no ingress")
                    + f" from {source}"
                )
            lines.append("")

        if roles:
            lines.append(
                f"**{len(roles)} IAM role{'s' if len(roles) != 1 else ''}**, "
                "assumed by the service rather than by a person. No long-lived "
                "access key exists for them."
            )
            lines.extend(f"• **{r.name}** (`{r.id}`)" for r in roles)
            lines.append("")

        for kind, note in (
            (Kind.CERTIFICATE, "TLS is terminated at the load balancer with an "
                               "ACM certificate; the private key never leaves ACM."),
            (Kind.WAF, "A WAF inspects requests before they reach the origin."),
            (Kind.SECRET_STORE, "The database credential is held in Secrets "
                                "Manager rather than in Terraform state."),
            (Kind.KEY_MANAGEMENT, "A customer-managed KMS key encrypts stored "
                                  "data, and every use is recorded in CloudTrail."),
        ):
            if spec.has(kind):
                lines.append(note)

        concerns = [f for f in result.findings
                    if f.code in {"open_admin_port", "open_app_port",
                                  "public_database", "public_bucket",
                                  "no_secret_store", "unencrypted_storage"}]
        if concerns:
            lines.append("")
            lines.append("Security findings on this design:")
            lines.extend(f"• {f.message}" for f in concerns)

        if not lines:
            return Answer(
                question=question,
                intent=Intent.SECURITY.value,
                text="This design has no security groups, IAM roles or "
                     "encryption resources, which usually means it contains "
                     "nothing that needs them.",
                sources=[Source.SPEC.value],
            )

        return Answer(
            question=question,
            intent=Intent.SECURITY.value,
            text="\n".join(lines),
            sources=[Source.SPEC.value, Source.EXPLAIN.value]
                    + ([Source.VALIDATOR.value] if concerns else []),
            resources=[r.id for r in groups + roles],
            findings=[f.to_dict() for f in concerns],
        )

    # ------------------------------------------------------------------
    # architecture
    # ------------------------------------------------------------------

    def _architecture(self, question: str, result) -> Answer:
        spec = result.spec
        requested = [r for r in spec.resources if r.origin is Origin.EXPLICIT]
        required = [r for r in spec.resources if r.origin is not Origin.EXPLICIT]
        errors = [f for f in result.findings if f.severity == "error"]

        lines = [
            f"**{spec.name}** — {spec.environment} in {spec.region}, "
            f"{len(spec.resources)} resources.",
            "",
            spec.summary,
            "",
            f"**{len(requested)} you asked for:** "
            + ", ".join(f"{r.name}" + (f" ×{r.count}" if r.count > 1 else "")
                        for r in requested),
            "",
            f"**{len(required)} added as mandatory dependencies:** "
            + ", ".join(r.name for r in required),
            "",
            f"Estimated cost is about **${result.estimated_monthly_cost:.2f} a "
            f"month**, and validation reports "
            + (f"**{len(errors)} error{'s' if len(errors) != 1 else ''}** that "
               "would fail at apply." if errors
               else "nothing that blocks deployment."),
        ]

        if spec.exclusions:
            lines.append("")
            lines.append("Deliberately excluded: " + ", ".join(
                f"{e.kind.value} (you said “{e.cue}”)" for e in spec.exclusions))

        if spec.warnings:
            lines.append("")
            lines.append("Limitations: " + " ".join(spec.warnings))

        return Answer(
            question=question,
            intent=Intent.ARCHITECTURE.value,
            text="\n".join(lines),
            sources=[Source.SPEC.value, Source.TRACE.value,
                     Source.VALIDATOR.value, Source.COST.value],
            resources=[r.id for r in requested],
        )

    # ------------------------------------------------------------------
    # deployment
    # ------------------------------------------------------------------

    def _deployment(self, question: str, result) -> Answer:
        errors = [f for f in result.findings if f.severity == "error"]
        blocking = [r for r in result.recommendations if r.priority == "critical"]

        lines = [
            "**Deploying this project**",
            "",
            "1. Download the project and unzip it.",
            "2. `terraform init` — downloads the AWS provider.",
            "3. `terraform plan` — read this before applying anything.",
            "4. `terraform apply` — creates real, billable resources.",
            "",
        ]

        if errors:
            lines.append(
                f"**{len(errors)} validation error"
                f"{'s' if len(errors) != 1 else ''} would fail at apply.** "
                "Fix these first:")
            lines.extend(f"• {f.message}" for f in errors)
            lines.append("")
        else:
            lines.append("Validation reports nothing that blocks apply.")
            lines.append("")

        if blocking:
            lines.append("Critical recommendations to consider first:")
            lines.extend(f"• {r.title} — {r.action}" for r in blocking)
            lines.append("")

        lines.extend([
            "Before applying:",
            "• Terraform state is local by default — configure an S3 backend "
            "with locking before anyone else runs this.",
            "• Review the security group rules; a generated design opens what "
            "the requirement implied, which may be more than you want.",
            f"• Confirm the region is right — this deploys into **{result.spec.region}**.",
            "• Generated passwords live in state. Move them to Secrets Manager.",
        ])

        return Answer(
            question=question,
            intent=Intent.DEPLOYMENT.value,
            text="\n".join(lines),
            sources=[Source.VALIDATOR.value, Source.SPEC.value, Source.OPTIMIZER.value],
            findings=[f.to_dict() for f in errors],
            recommendations=[r.to_dict() for r in blocking],
        )

    # ------------------------------------------------------------------
    # fallback
    # ------------------------------------------------------------------

    def _fallback(self, question: str, result, intent: Intent) -> Answer:
        """No deterministic answer. Use the model if there is one, else say so."""
        general = bool(_GENERAL_KNOWLEDGE.search(question.lower()))

        if self.llm is not None and getattr(self.llm, "available", False):
            try:
                text = self.llm.answer(question, result.spec)
                return Answer(
                    question=question,
                    intent=intent.value,
                    text=text,
                    sources=[Source.LLM.value],
                    deterministic=False,
                    confidence=0.6,
                )
            except Exception:  # noqa: BLE001 - a model failure must not break the assistant
                pass

        if general:
            return Answer(
                question=question,
                intent=intent.value,
                text=(
                    "That is a general AWS question rather than one about this "
                    "design, and no language model is configured, so I will not "
                    "guess at an answer.\n\n"
                    "Set `ANTHROPIC_API_KEY` on the server to enable general "
                    "questions. Everything about *this* architecture — why a "
                    "resource exists, what it costs, how to improve it — is "
                    "answered without one."
                ),
                sources=[],
                deterministic=True,
                confidence=1.0,
            )

        return Answer(
            question=question,
            intent=Intent.UNKNOWN.value,
            text=(
                "I could not match that to anything in this design.\n\n"
                "I can tell you why any resource exists, what policy rule "
                "required it, what it costs, how the networking and security "
                "are arranged, what validation found, how to reduce cost or "
                "improve security, how two services compare, and how to deploy "
                "the project."
            ),
            sources=[],
            deterministic=True,
        )

    # ------------------------------------------------------------------

    def suggestions(self, result) -> list[str]:
        """Questions worth asking about *this* design, not a fixed list."""
        spec = result.spec
        out: list[str] = ["Explain this architecture"]

        required = [r for r in spec.resources if r.origin is not Origin.EXPLICIT]
        if required:
            out.append(f"Why was {required[0].name} added?")

        if any(f.severity == "error" for f in result.findings):
            out.append("Explain the validation errors")
        elif result.findings:
            out.append("Explain the validation findings")

        if any(r.monthly_delta_usd < 0 for r in result.recommendations):
            out.append("How can I reduce the cost?")
        if any(r.category == "security" for r in result.recommendations):
            out.append("How can I improve security?")

        if spec.has(Kind.VPC):
            out.append("Explain the networking")
        if spec.has(Kind.VM) or spec.has(Kind.AUTOSCALING_GROUP):
            out.append("Compare EC2 vs ECS")
        elif spec.has(Kind.CONTAINER_SERVICE):
            out.append("Compare ECS vs EKS")
        if spec.has(Kind.SQL_DATABASE):
            out.append("Compare RDS vs Aurora")

        out.append("How do I deploy this?")
        return out[:7]
