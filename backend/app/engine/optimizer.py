"""Architecture optimisation: recommendations over a completed design.

This module answers "what would make this better?" — a different question from
the one :mod:`app.engine.validator` answers, which is "what is wrong with it?".
A finding says the design is broken or unsafe. A recommendation says the design
works and could be improved, and always at a stated cost.

**Recommendations are advice, never resources.** Nothing here mutates the
``InfrastructureSpec``, and nothing here reaches the Terraform generator. The
system's central guarantee is that a generated resource was either asked for or
is a mandatory dependency; an optimiser that quietly added a NAT gateway
because it seemed wise would destroy that guarantee. So the optimiser is
allowed to *say* "add a NAT gateway", and is not allowed to add one.

Every rule is deterministic and reads only the spec. Two runs over the same
design produce the same recommendations in the same order, which is what makes
them citable in a report.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from app.engine.validator import MONTHLY_COST_HINTS
from app.models.ir import InfrastructureSpec, Kind, Origin

Category = Literal[
    "security", "cost", "reliability", "performance",
    "operations", "networking", "compliance",
]
Priority = Literal["critical", "high", "medium", "low"]
Difficulty = Literal["trivial", "moderate", "involved"]

#: AWS Well-Architected pillars, used verbatim so a reviewer can map a
#: recommendation onto the framework without a translation table.
PILLARS: dict[Category, str] = {
    "security": "Security",
    "cost": "Cost Optimization",
    "reliability": "Reliability",
    "performance": "Performance Efficiency",
    "operations": "Operational Excellence",
    "networking": "Reliability",
    "compliance": "Security",
}

PRIORITY_RANK: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class Recommendation:
    """One improvement, with everything needed to decide whether to take it."""

    id: str
    category: Category
    priority: Priority
    title: str
    #: Why the current design warrants this. States the consequence, not a rule name.
    reason: str
    #: What to actually do, in the user's terms — a prompt to rephrase or a
    #: Terraform argument to change.
    action: str
    #: Resource ids this affects. Empty means it is a design-level suggestion.
    resources: list[str] = field(default_factory=list)
    difficulty: Difficulty = "moderate"
    #: Monthly USD change if adopted. Negative is a saving. Zero means the
    #: change is free, which is worth knowing.
    monthly_delta_usd: float = 0.0
    #: How confident the rule is that this applies. Rules that read an explicit
    #: property are certain; rules inferring intent are not.
    confidence: float = 1.0
    pillar: str = ""

    def __post_init__(self) -> None:
        if not self.pillar:
            self.pillar = PILLARS[self.category]

    def to_dict(self) -> dict:
        return asdict(self)


class Optimizer:
    """Runs every rule over a mapped spec and returns ranked recommendations."""

    def analyse(self, spec: InfrastructureSpec) -> list[Recommendation]:
        if not spec.resources or spec.unsupported_provider is not None:
            return []

        out: list[Recommendation] = []
        for rule in (
            self._security,
            self._cost,
            self._reliability,
            self._performance,
            self._networking,
            self._operations,
            self._compliance,
        ):
            out.extend(rule(spec))

        # Stable order: severity first, then id, so two runs agree exactly.
        out.sort(key=lambda r: (PRIORITY_RANK[r.priority], r.id))
        return out

    # ------------------------------------------------------------------
    # security
    # ------------------------------------------------------------------

    def _security(self, spec: InfrastructureSpec) -> list[Recommendation]:
        out: list[Recommendation] = []

        databases = spec.of_kind(Kind.SQL_DATABASE, Kind.SQL_CLUSTER, Kind.CACHE)
        if databases and not spec.has(Kind.SECRET_STORE):
            out.append(Recommendation(
                id="sec.secrets_manager",
                category="security",
                priority="high",
                title="Store the database credential in Secrets Manager",
                reason=(
                    "The generated password currently exists only in Terraform "
                    "state. Anyone who can read the state file can read the "
                    "credential, and state is commonly stored in a shared bucket."
                ),
                action='Add "with Secrets Manager" to the requirement.',
                resources=[r.id for r in databases],
                difficulty="trivial",
                monthly_delta_usd=0.40,
            ))

        if spec.has(Kind.OBJECT_STORAGE) and not spec.has(Kind.KEY_MANAGEMENT):
            out.append(Recommendation(
                id="sec.cmk_encryption",
                category="security",
                priority="low",
                title="Use a customer-managed KMS key for stored data",
                reason=(
                    "Buckets are encrypted with the AWS-managed key by default. "
                    "A customer-managed key adds key rotation you control and an "
                    "audit trail of every decrypt."
                ),
                action='Add "with a KMS key" to the requirement.',
                resources=[r.id for r in spec.of_kind(Kind.OBJECT_STORAGE)],
                difficulty="trivial",
                monthly_delta_usd=1.0,
            ))

        internet_facing = [
            r for r in spec.of_kind(Kind.LOAD_BALANCER, Kind.NETWORK_LOAD_BALANCER)
            if not r.properties.get("internal")
        ]
        if internet_facing and not spec.has(Kind.WAF):
            out.append(Recommendation(
                id="sec.waf",
                category="security",
                priority="medium",
                title="Put a WAF in front of the internet-facing load balancer",
                reason=(
                    "An internet-facing balancer forwards every request that "
                    "reaches it, including automated probes for known "
                    "application vulnerabilities."
                ),
                action='Add "with a WAF" to the requirement.',
                resources=[r.id for r in internet_facing],
                difficulty="trivial",
                monthly_delta_usd=6.0,
            ))

        # TLS on a public listener is not optional in any serious deployment.
        plain_http = [
            r for r in internet_facing if not r.properties.get("https")
        ]
        if plain_http:
            out.append(Recommendation(
                id="sec.tls",
                category="security",
                priority="high",
                title="Terminate TLS at the load balancer",
                reason=(
                    "The listener currently serves plain HTTP, so credentials "
                    "and session cookies travel unencrypted between the client "
                    "and the balancer."
                ),
                action='Add "with HTTPS" to the requirement; an ACM certificate '
                       "is created as a mandatory dependency of the listener.",
                resources=[r.id for r in plain_http],
                difficulty="trivial",
                monthly_delta_usd=0.0,
            ))

        bastions = spec.of_kind(Kind.BASTION)
        if bastions:
            out.append(Recommendation(
                id="sec.ssm_over_bastion",
                category="security",
                priority="medium",
                title="Consider Session Manager instead of a bastion host",
                reason=(
                    "A bastion is an always-on instance with an open "
                    "administrative port and its own patching burden. Session "
                    "Manager reaches private instances with no inbound port and "
                    "no host to maintain."
                ),
                action="Remove the bastion and use SSM Session Manager for "
                       "administrative access.",
                resources=[r.id for r in bastions],
                difficulty="moderate",
                monthly_delta_usd=-8.0,
                confidence=0.75,
            ))

        return out

    # ------------------------------------------------------------------
    # cost
    # ------------------------------------------------------------------

    def _cost(self, spec: InfrastructureSpec) -> list[Recommendation]:
        out: list[Recommendation] = []

        nats = spec.of_kind(Kind.NAT_GATEWAY)
        if nats and spec.availability_zones > 1:
            # One per AZ is the zonal-resilience default; collapsing to one
            # trades that resilience for roughly its own cost per zone.
            saving = MONTHLY_COST_HINTS[Kind.NAT_GATEWAY] * (spec.availability_zones - 1)
            out.append(Recommendation(
                id="cost.single_nat",
                category="cost",
                priority="medium" if spec.environment != "prod" else "low",
                title=f"Use one NAT gateway instead of {spec.availability_zones}",
                reason=(
                    "NAT gateways are billed hourly per zone plus per gigabyte, "
                    "and are usually the largest line in a small design. One per "
                    "zone buys resilience to a zone failure, which a "
                    f"{spec.environment} environment may not need."
                ),
                action='Add "a single NAT gateway" to the requirement.',
                resources=[r.id for r in nats],
                difficulty="trivial",
                monthly_delta_usd=-saving,
                confidence=0.9 if spec.environment != "prod" else 0.6,
            ))

        compute = spec.of_kind(Kind.VM, Kind.AUTOSCALING_GROUP)
        if compute and spec.environment == "prod":
            monthly = sum(
                MONTHLY_COST_HINTS.get(r.kind, 0.0) * r.count for r in compute
            )
            if monthly > 0:
                out.append(Recommendation(
                    id="cost.commit_discount",
                    category="cost",
                    priority="medium",
                    title="Cover steady-state compute with a Savings Plan",
                    reason=(
                        "Production compute runs continuously, which is exactly "
                        "the usage a one-year commitment is priced for. A "
                        "Compute Savings Plan typically returns around 30% "
                        "against on-demand for the same instances."
                    ),
                    action="Buy a one-year Compute Savings Plan covering the "
                           "steady-state portion of this fleet.",
                    resources=[r.id for r in compute],
                    difficulty="moderate",
                    monthly_delta_usd=-round(monthly * 0.3, 2),
                    confidence=0.8,
                ))

        if compute and spec.environment != "prod":
            out.append(Recommendation(
                id="cost.spot_nonprod",
                category="cost",
                priority="low",
                title="Run non-production compute on Spot capacity",
                reason=(
                    "A development environment tolerates interruption, and Spot "
                    "capacity is priced well below on-demand for the same "
                    "instance types."
                ),
                action="Set the launch template to request Spot capacity.",
                resources=[r.id for r in compute],
                difficulty="moderate",
                monthly_delta_usd=-round(
                    sum(MONTHLY_COST_HINTS.get(r.kind, 0.0) * r.count for r in compute) * 0.6, 2,
                ),
                confidence=0.7,
            ))

        buckets = spec.of_kind(Kind.OBJECT_STORAGE)
        if buckets:
            out.append(Recommendation(
                id="cost.s3_lifecycle",
                category="cost",
                priority="low",
                title="Add a lifecycle policy to object storage",
                reason=(
                    "Objects stay in Standard storage indefinitely unless a "
                    "lifecycle rule moves them. Data that is rarely read after "
                    "its first month costs several times what it needs to."
                ),
                action="Add a lifecycle rule transitioning objects to "
                       "Infrequent Access after 30 days.",
                resources=[r.id for r in buckets],
                difficulty="trivial",
                monthly_delta_usd=0.0,
                confidence=0.65,
            ))

        return out

    # ------------------------------------------------------------------
    # reliability
    # ------------------------------------------------------------------

    def _reliability(self, spec: InfrastructureSpec) -> list[Recommendation]:
        out: list[Recommendation] = []

        if spec.availability_zones < 2 and spec.environment == "prod":
            out.append(Recommendation(
                id="rel.multi_az",
                category="reliability",
                priority="critical",
                title="Spread this production design across two availability zones",
                reason=(
                    "Everything in this design sits in one zone. An AWS zone "
                    "failure is a documented, recurring event, and this "
                    "architecture has no answer to one."
                ),
                action='Add "highly available" or "across 2 availability zones" '
                       "to the requirement.",
                resources=[],
                difficulty="trivial",
                monthly_delta_usd=0.0,
            ))

        for db in spec.of_kind(Kind.SQL_DATABASE):
            if not db.properties.get("multi_az"):
                out.append(Recommendation(
                    id=f"rel.multi_az_db.{db.id}",
                    category="reliability",
                    priority="high" if spec.environment == "prod" else "low",
                    title="Enable Multi-AZ on the database",
                    reason=(
                        "A single-AZ database is unavailable for the duration of "
                        "any zone incident or maintenance event, with no standby "
                        "to fail over to."
                    ),
                    action='Add "Multi-AZ" to the requirement.',
                    resources=[db.id],
                    difficulty="trivial",
                    monthly_delta_usd=MONTHLY_COST_HINTS.get(db.kind, 25.0),
                ))

        singles = [
            r for r in spec.of_kind(Kind.VM)
            if r.count == 1 and r.origin is Origin.EXPLICIT
        ]
        if singles and spec.environment == "prod":
            out.append(Recommendation(
                id="rel.scale_out",
                category="reliability",
                priority="high",
                title="Replace the single instance with a scaling group",
                reason=(
                    "One instance is a single point of failure and cannot be "
                    "patched without downtime. A scaling group of two replaces "
                    "instances without an outage."
                ),
                action='Ask for "an auto scaling group" instead of one instance.',
                resources=[r.id for r in singles],
                difficulty="moderate",
                monthly_delta_usd=MONTHLY_COST_HINTS.get(Kind.VM, 8.0),
            ))

        stateful = spec.of_kind(
            Kind.SQL_DATABASE, Kind.SQL_CLUSTER, Kind.DATA_WAREHOUSE,
        )
        if stateful and spec.environment == "prod":
            out.append(Recommendation(
                id="rel.cross_region_backup",
                category="reliability",
                priority="medium",
                title="Copy backups to a second region",
                reason=(
                    "Backups currently live in the same region as the data they "
                    "protect. A regional event takes both, which is the scenario "
                    "a disaster recovery plan exists for."
                ),
                action="Enable cross-region automated backup copies, or add AWS "
                       "Backup with a second-region vault.",
                resources=[r.id for r in stateful],
                difficulty="moderate",
                monthly_delta_usd=4.0,
                confidence=0.85,
            ))

        return out

    # ------------------------------------------------------------------
    # performance
    # ------------------------------------------------------------------

    def _performance(self, spec: InfrastructureSpec) -> list[Recommendation]:
        out: list[Recommendation] = []

        has_database = spec.has(Kind.SQL_DATABASE) or spec.has(Kind.SQL_CLUSTER)
        if has_database and not spec.has(Kind.CACHE):
            out.append(Recommendation(
                id="perf.cache",
                category="performance",
                priority="low",
                title="Add a cache in front of the database",
                reason=(
                    "Read-heavy workloads repeat the same queries. A cache "
                    "absorbs those reads, which both lowers latency and lets the "
                    "database instance stay smaller."
                ),
                action='Add "with a Redis cache" to the requirement.',
                resources=[r.id for r in spec.of_kind(Kind.SQL_DATABASE, Kind.SQL_CLUSTER)],
                difficulty="moderate",
                monthly_delta_usd=MONTHLY_COST_HINTS.get(Kind.CACHE, 13.0),
                confidence=0.6,
            ))

        buckets = spec.of_kind(Kind.OBJECT_STORAGE)
        if buckets and not spec.has(Kind.CDN):
            out.append(Recommendation(
                id="perf.cdn",
                category="performance",
                priority="low",
                title="Serve static assets through CloudFront",
                reason=(
                    "Requests currently reach the bucket's region from wherever "
                    "the user is. A CDN serves them from an edge location and "
                    "removes the per-request cost of origin reads."
                ),
                action='Add "served through CloudFront" to the requirement.',
                resources=[r.id for r in buckets],
                difficulty="moderate",
                monthly_delta_usd=MONTHLY_COST_HINTS.get(Kind.CDN, 5.0),
                confidence=0.55,
            ))

        for cluster in spec.of_kind(Kind.SQL_CLUSTER):
            if int(cluster.properties.get("instances", 1)) < 2:
                out.append(Recommendation(
                    id=f"perf.aurora_reader.{cluster.id}",
                    category="performance",
                    priority="low",
                    title="Add an Aurora reader instance",
                    reason=(
                        "The cluster has a writer only, so every read competes "
                        "with writes on one instance. A reader also becomes the "
                        "failover target, which shortens recovery."
                    ),
                    action='Ask for "highly available" so the cluster is created '
                           "with a writer and a reader.",
                    resources=[cluster.id],
                    difficulty="trivial",
                    monthly_delta_usd=45.0,
                ))

        return out

    # ------------------------------------------------------------------
    # networking
    # ------------------------------------------------------------------

    def _networking(self, spec: InfrastructureSpec) -> list[Recommendation]:
        out: list[Recommendation] = []

        private_data = [
            r for r in spec.of_kind(Kind.SQL_DATABASE, Kind.SQL_CLUSTER, Kind.CACHE)
            if r.properties.get("subnet_band") != "private"
        ]
        if private_data:
            out.append(Recommendation(
                id="net.private_data",
                category="networking",
                priority="critical",
                title="Move data services into private subnets",
                reason=(
                    "A database in a public subnet is reachable from the "
                    "internet if a security group is ever misconfigured, and it "
                    "gains nothing from being there."
                ),
                action='Add "in private subnets" to the requirement.',
                resources=[r.id for r in private_data],
                difficulty="trivial",
                monthly_delta_usd=0.0,
            ))

        buckets = spec.of_kind(Kind.OBJECT_STORAGE)
        if buckets and spec.has(Kind.NAT_GATEWAY):
            out.append(Recommendation(
                id="net.s3_endpoint",
                category="networking",
                priority="medium",
                title="Add a gateway VPC endpoint for S3",
                reason=(
                    "Traffic from private subnets to S3 currently leaves through "
                    "the NAT gateway and is billed per gigabyte. A gateway "
                    "endpoint routes it inside the VPC at no charge."
                ),
                action="Add an aws_vpc_endpoint of type Gateway for "
                       "com.amazonaws.<region>.s3.",
                resources=[r.id for r in buckets],
                difficulty="trivial",
                monthly_delta_usd=-5.0,
                confidence=0.85,
            ))

        if spec.has(Kind.VPC) and not spec.has(Kind.MONITORING):
            out.append(Recommendation(
                id="net.flow_logs",
                category="networking",
                priority="low",
                title="Enable VPC flow logs",
                reason=(
                    "Without flow logs there is no record of what talked to what, "
                    "which makes both a security investigation and a "
                    "connectivity problem far harder to resolve."
                ),
                action="Enable flow logs on the VPC, delivered to CloudWatch "
                       "Logs or S3.",
                resources=[r.id for r in spec.of_kind(Kind.VPC)],
                difficulty="trivial",
                monthly_delta_usd=2.5,
            ))

        return out

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------

    def _operations(self, spec: InfrastructureSpec) -> list[Recommendation]:
        out: list[Recommendation] = []

        watchable = spec.of_kind(
            Kind.VM, Kind.AUTOSCALING_GROUP, Kind.SQL_DATABASE, Kind.SQL_CLUSTER,
            Kind.CONTAINER_SERVICE, Kind.KUBERNETES_CLUSTER,
        )
        if watchable and not spec.has(Kind.MONITORING):
            out.append(Recommendation(
                id="ops.monitoring",
                category="operations",
                priority="high",
                title="Add CloudWatch alarms",
                reason=(
                    "Nothing in this design reports when it degrades. The first "
                    "indication of a problem will be a user telling you."
                ),
                action='Add "with CloudWatch monitoring" to the requirement.',
                resources=[r.id for r in watchable],
                difficulty="trivial",
                monthly_delta_usd=3.0,
            ))

        if spec.environment == "prod":
            out.append(Recommendation(
                id="ops.remote_state",
                category="operations",
                priority="high",
                title="Move Terraform state to a remote backend",
                reason=(
                    "The generated project uses local state. Local state cannot "
                    "be shared, is not locked against concurrent applies, and is "
                    "lost with the machine holding it."
                ),
                action="Configure an S3 backend with DynamoDB state locking "
                       "before the first apply.",
                resources=[],
                difficulty="moderate",
                monthly_delta_usd=1.0,
            ))

        return out

    # ------------------------------------------------------------------
    # compliance
    # ------------------------------------------------------------------

    def _compliance(self, spec: InfrastructureSpec) -> list[Recommendation]:
        """Only what the design demonstrably lacks.

        Deliberately conservative: a compliance claim that is not backed by a
        control in the generated code is worse than no claim at all.
        """
        out: list[Recommendation] = []

        stores_data = spec.of_kind(
            Kind.SQL_DATABASE, Kind.SQL_CLUSTER, Kind.OBJECT_STORAGE,
            Kind.FILE_STORAGE, Kind.DATA_WAREHOUSE, Kind.NOSQL_TABLE,
        )
        if stores_data and not spec.has(Kind.MONITORING):
            out.append(Recommendation(
                id="comp.audit_trail",
                category="compliance",
                priority="medium",
                title="Enable CloudTrail for an audit trail",
                reason=(
                    "This design stores data but records no API activity. Every "
                    "common framework — SOC 2, PCI DSS, HIPAA — requires an "
                    "audit trail of who did what."
                ),
                action="Enable CloudTrail with a dedicated log bucket and log "
                       "file validation.",
                resources=[r.id for r in stores_data],
                difficulty="moderate",
                monthly_delta_usd=2.0,
            ))

        unencrypted = [
            r for r in stores_data
            if r.properties.get("encrypted") is False
            or r.properties.get("storage_encrypted") is False
        ]
        if unencrypted:
            out.append(Recommendation(
                id="comp.encryption_at_rest",
                category="compliance",
                priority="high",
                title="Encrypt data at rest",
                reason=(
                    "Encryption at rest is a baseline control in every common "
                    "framework, and these stores do not have it enabled."
                ),
                action='Add "encrypted" to the requirement.',
                resources=[r.id for r in unencrypted],
                difficulty="trivial",
                monthly_delta_usd=0.0,
            ))

        return out


def summarise(recommendations: list[Recommendation]) -> dict:
    """Counts and net cost impact, for the response header and reports."""
    by_category: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for item in recommendations:
        by_category[item.category] = by_category.get(item.category, 0) + 1
        by_priority[item.priority] = by_priority.get(item.priority, 0) + 1

    savings = sum(r.monthly_delta_usd for r in recommendations if r.monthly_delta_usd < 0)
    spend = sum(r.monthly_delta_usd for r in recommendations if r.monthly_delta_usd > 0)

    return {
        "total": len(recommendations),
        "by_category": by_category,
        "by_priority": by_priority,
        "potential_monthly_saving_usd": round(abs(savings), 2),
        "additional_monthly_spend_usd": round(spend, 2),
    }
