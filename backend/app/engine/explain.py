"""Per-resource explanation: everything known about why a resource is there.

This module is a *view* over the specification, not an extension of it. It
adds no resource, changes no property, and holds no state; running it twice
produces the same answer because it is a pure function of the spec, the
findings and the generated Terraform.

Two kinds of content appear here, and the distinction matters:

* **Provenance** — origin, policy rule, triggering resource, evidence span,
  confidence, dependencies, cost, Terraform. All of this is read from the
  spec or computed from it. It is specific to *this* design.
* **Service knowledge** — security notes, networking notes, alternatives,
  best practices. These are fixed tables keyed by resource kind, written
  once and reviewed like any other code. They are general to the service.

Nothing is generated at request time by a model. An explanation that varied
between two runs of the same prompt would be worthless as an audit record,
and a fabricated one would be worse than none: the point of this system is
that a reviewer can check every claim it makes.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from app.engine.validator import MONTHLY_COST_HINTS
from app.models.ir import InfrastructureSpec, Kind, Origin, Resource

# --------------------------------------------------------------------------
# Service knowledge. Fixed tables, keyed by kind.
# --------------------------------------------------------------------------

#: The Well-Architected pillar a resource most directly serves. Where a
#: resource serves several, the one named is the reason it usually appears.
PILLAR: dict[Kind, str] = {
    Kind.VPC: "Security",
    Kind.SUBNET_PUBLIC: "Security",
    Kind.SUBNET_PRIVATE: "Security",
    Kind.SECURITY_GROUP: "Security",
    Kind.IAM_ROLE: "Security",
    Kind.SECRET_STORE: "Security",
    Kind.KEY_MANAGEMENT: "Security",
    Kind.WAF: "Security",
    Kind.CERTIFICATE: "Security",
    Kind.INTERNET_GATEWAY: "Reliability",
    Kind.NAT_GATEWAY: "Reliability",
    Kind.ROUTE_TABLE: "Reliability",
    Kind.LOAD_BALANCER: "Reliability",
    Kind.NETWORK_LOAD_BALANCER: "Reliability",
    Kind.GATEWAY_LOAD_BALANCER: "Reliability",
    Kind.TARGET_GROUP: "Reliability",
    Kind.AUTOSCALING_GROUP: "Reliability",
    Kind.SQL_DATABASE: "Reliability",
    Kind.SQL_CLUSTER: "Reliability",
    Kind.ELASTIC_IP: "Reliability",
    Kind.VM: "Performance Efficiency",
    Kind.CONTAINER_SERVICE: "Performance Efficiency",
    Kind.KUBERNETES_CLUSTER: "Performance Efficiency",
    Kind.FUNCTION: "Performance Efficiency",
    Kind.CACHE: "Performance Efficiency",
    Kind.CDN: "Performance Efficiency",
    Kind.NOSQL_TABLE: "Performance Efficiency",
    Kind.DATA_WAREHOUSE: "Performance Efficiency",
    Kind.OBJECT_STORAGE: "Cost Optimization",
    Kind.FILE_STORAGE: "Cost Optimization",
    Kind.CONTAINER_REGISTRY: "Operational Excellence",
    Kind.MONITORING: "Operational Excellence",
    Kind.QUEUE: "Operational Excellence",
    Kind.TOPIC: "Operational Excellence",
    Kind.EVENT_BUS: "Operational Excellence",
    Kind.BASTION: "Security",
    Kind.API_GATEWAY: "Performance Efficiency",
    Kind.DNS_ZONE: "Reliability",
}

SECURITY_NOTES: dict[Kind, str] = {
    Kind.VM: "Reachable only through its security group. Grant permissions "
             "through the attached IAM role rather than embedded keys.",
    Kind.AUTOSCALING_GROUP: "Instances are replaced rather than patched in "
                            "place, so a new AMI reaches the whole fleet.",
    Kind.SQL_DATABASE: "Not publicly accessible, and the master password is "
                       "generated rather than written into the configuration.",
    Kind.SQL_CLUSTER: "Storage is encrypted and the cluster is not publicly "
                      "accessible. Credentials are generated at apply time.",
    Kind.OBJECT_STORAGE: "Public access is blocked at the bucket level and "
                         "server-side encryption is enabled.",
    Kind.SECURITY_GROUP: "A stateful allow-list. Return traffic for an allowed "
                         "connection is permitted automatically, so egress "
                         "rules govern new outbound connections only.",
    Kind.IAM_ROLE: "Assumed by the service rather than by a person. No long "
                   "lived access key exists for it.",
    Kind.LOAD_BALANCER: "Terminates client connections, so instances never "
                        "receive traffic directly from the internet.",
    Kind.NETWORK_LOAD_BALANCER: "Operates at layer 4 and preserves the client "
                                "address, so instance security groups see the "
                                "real source IP.",
    Kind.BASTION: "An internet-facing host with an administrative port open. "
                  "It is the most exposed component in any design containing "
                  "one.",
    Kind.CERTIFICATE: "Validated through DNS. The private key never leaves "
                      "ACM and cannot be exported.",
    Kind.SECRET_STORE: "Keeps the credential out of Terraform state, which is "
                       "otherwise the weakest link in a generated project.",
    Kind.KEY_MANAGEMENT: "Every use of the key is recorded in CloudTrail, "
                         "which is what makes key access auditable.",
    Kind.CACHE: "Has no authentication by default; its security group is the "
                "only thing restricting access.",
    Kind.FUNCTION: "Runs with the permissions of its execution role only. "
                   "Environment variables are visible to anyone who can read "
                   "the function configuration.",
    Kind.WAF: "Inspects requests before they reach the origin. Rules are "
              "evaluated in order and the first match decides.",
    Kind.NOSQL_TABLE: "Encrypted at rest by default. Access is controlled by "
                      "IAM policy rather than by a network boundary.",
}

NETWORKING_NOTES: dict[Kind, str] = {
    Kind.VPC: "An isolated network. Nothing inside it is reachable from the "
              "internet without an explicit route and an open security group.",
    Kind.SUBNET_PUBLIC: "Has a route to the internet gateway, which is what "
                        "makes it public. One per availability zone.",
    Kind.SUBNET_PRIVATE: "No route to the internet gateway. Outbound traffic "
                         "requires a NAT gateway; inbound is impossible.",
    Kind.INTERNET_GATEWAY: "The VPC's only path to and from the internet. "
                           "Highly available and horizontally scaled by AWS.",
    Kind.NAT_GATEWAY: "Allows outbound connections from private subnets and "
                      "permits no inbound ones. Zonal: a zone failure takes "
                      "the gateway in it.",
    Kind.ROUTE_TABLE: "Decides where traffic leaving a subnet goes. The "
                      "difference between a public and a private subnet is "
                      "one route in this table.",
    Kind.LOAD_BALANCER: "Requires subnets in at least two availability zones. "
                        "Resolves to addresses that change, so clients must "
                        "use the DNS name rather than an IP.",
    Kind.VM: "Placed in a subnet, and reachable according to that subnet's "
             "routes and its own security group.",
    Kind.SQL_DATABASE: "Placed in a subnet group spanning the private "
                       "subnets, so it is reachable only from inside the VPC.",
    Kind.SQL_CLUSTER: "Spans the private subnets. The writer and reader "
                      "endpoints are separate DNS names.",
    Kind.CACHE: "Reachable only from inside the VPC. There is no public "
                "endpoint option.",
    Kind.CDN: "Serves from edge locations worldwide rather than from the "
              "origin's region.",
    Kind.API_GATEWAY: "A regional public endpoint. It sits outside the VPC "
                      "unless a VPC link is configured.",
    Kind.FUNCTION: "Runs outside your VPC unless VPC configuration is added, "
                   "in which case it needs a NAT gateway for internet access.",
    Kind.OBJECT_STORAGE: "Reached over the public endpoint by default. A "
                         "gateway VPC endpoint keeps that traffic internal "
                         "and removes NAT charges for it.",
    Kind.ELASTIC_IP: "A static address that survives instance replacement.",
    Kind.BASTION: "Sits in a public subnet by necessity, since it must be "
                  "reachable to be useful.",
}

OPERATIONAL_NOTES: dict[Kind, str] = {
    Kind.AUTOSCALING_GROUP: "Replaces unhealthy instances automatically. "
                            "Capacity changes need a scaling policy, which is "
                            "generated alongside it.",
    Kind.VM: "Patching and replacement are manual. Consider a scaling group "
             "if this must stay available during maintenance.",
    Kind.SQL_DATABASE: "Backups run in the configured window and minor "
                       "version upgrades apply automatically.",
    Kind.SQL_CLUSTER: "Failover to a reader is automatic and typically "
                      "completes in under a minute.",
    Kind.KUBERNETES_CLUSTER: "The control plane is managed; node groups are "
                             "yours to upgrade, and the version skew between "
                             "them is bounded.",
    Kind.CONTAINER_SERVICE: "Task definitions are immutable. A deployment "
                            "creates a new revision rather than editing one.",
    Kind.FUNCTION: "Scales to zero. Cold start latency applies to the first "
                   "request after an idle period.",
    Kind.MONITORING: "Alarms need a destination to be useful; an SNS topic is "
                     "generated to receive them.",
    Kind.OBJECT_STORAGE: "Versioning is enabled, so an overwritten object is "
                         "recoverable and storage grows until a lifecycle "
                         "rule trims it.",
    Kind.CONTAINER_REGISTRY: "Image tags are immutable, so a tag always "
                             "refers to the image it originally named.",
}

#: Services that could fill the same role, with the trade-off that decides.
#: Written as a comparison rather than a list, because "you could also use X"
#: is not useful without the reason to.
ALTERNATIVES: dict[Kind, list[dict[str, str]]] = {
    Kind.VM: [
        {"service": "ECS Fargate",
         "when": "The workload is containerised and you would rather not "
                 "patch instances. Costs more per unit of compute; costs less "
                 "in operational time."},
        {"service": "Lambda",
         "when": "Work is event-driven and bursty. Scales to zero, but a "
                 "15-minute execution ceiling and cold starts apply."},
    ],
    Kind.AUTOSCALING_GROUP: [
        {"service": "ECS Fargate service",
         "when": "You want scaling without managing the instances underneath."},
        {"service": "EKS managed node group",
         "when": "You already run Kubernetes and want one scheduler for "
                 "everything."},
    ],
    Kind.CONTAINER_SERVICE: [
        {"service": "EKS",
         "when": "You need the Kubernetes API, its ecosystem, or portability "
                 "between clouds. Adds a control plane charge and real "
                 "operational complexity."},
        {"service": "EC2 with Docker",
         "when": "You want full control of the host and are prepared to "
                 "operate it."},
    ],
    Kind.KUBERNETES_CLUSTER: [
        {"service": "ECS Fargate",
         "when": "You want containers without Kubernetes. Substantially "
                 "simpler, no control plane charge, AWS-only."},
        {"service": "App Runner",
         "when": "The workload is a single stateless service and you want no "
                 "infrastructure at all."},
    ],
    Kind.SQL_DATABASE: [
        {"service": "Aurora",
         "when": "You need faster failover, readers that scale independently, "
                 "or storage that grows automatically. Costs more at rest."},
        {"service": "DynamoDB",
         "when": "Access patterns are known and key-based. Removes capacity "
                 "planning; removes joins too."},
    ],
    Kind.SQL_CLUSTER: [
        {"service": "RDS single instance",
         "when": "The workload is modest and cost matters more than failover "
                 "speed."},
        {"service": "Aurora Serverless v2",
         "when": "Load is intermittent and you would rather pay for capacity "
                 "actually used."},
    ],
    Kind.CACHE: [
        {"service": "DynamoDB Accelerator",
         "when": "The thing being cached is DynamoDB, in which case DAX is "
                 "transparent to the application."},
        {"service": "Application-level caching",
         "when": "The working set is small enough to hold in process."},
    ],
    Kind.OBJECT_STORAGE: [
        {"service": "EFS",
         "when": "Several instances need POSIX file semantics rather than "
                 "object access."},
    ],
    Kind.FUNCTION: [
        {"service": "ECS Fargate",
         "when": "Executions run longer than 15 minutes or need persistent "
                 "connections."},
    ],
    Kind.NAT_GATEWAY: [
        {"service": "VPC endpoints",
         "when": "Outbound traffic is only to AWS services. Endpoints remove "
                 "both the NAT charge and the per-gigabyte fee for that "
                 "traffic."},
        {"service": "NAT instance",
         "when": "Cost matters more than availability and throughput; you "
                 "then own the patching."},
    ],
    Kind.LOAD_BALANCER: [
        {"service": "Network Load Balancer",
         "when": "You need layer 4, static IPs, or extreme throughput. Loses "
                 "path-based routing and header inspection."},
        {"service": "API Gateway",
         "when": "The target is Lambda, or you need per-route throttling and "
                 "authorisation."},
    ],
    Kind.DATA_WAREHOUSE: [
        {"service": "Athena over S3",
         "when": "Queries are infrequent. No cluster to run, priced per byte "
                 "scanned."},
    ],
    Kind.BASTION: [
        {"service": "SSM Session Manager",
         "when": "Almost always. No inbound port, no host to patch, and every "
                 "session is logged."},
    ],
}

BEST_PRACTICES: dict[Kind, list[str]] = {
    Kind.VM: [
        "Attach an IAM role rather than storing credentials on the instance.",
        "Use a launch template so the configuration is reproducible.",
    ],
    Kind.AUTOSCALING_GROUP: [
        "Spread across every availability zone the subnets cover.",
        "Set health checks to ELB so a failing application is replaced, not "
        "just a failing instance.",
    ],
    Kind.SQL_DATABASE: [
        "Enable Multi-AZ for anything production.",
        "Keep the database in private subnets with no public accessibility.",
        "Store the credential in Secrets Manager rather than in state.",
    ],
    Kind.SQL_CLUSTER: [
        "Run at least one reader so failover has a target.",
        "Enable deletion protection in production.",
    ],
    Kind.OBJECT_STORAGE: [
        "Block public access unless the bucket is genuinely a public origin.",
        "Add a lifecycle rule; Standard storage forever is rarely correct.",
    ],
    Kind.SECURITY_GROUP: [
        "Reference other security groups rather than CIDR ranges where "
        "possible, so rules survive an address change.",
        "Never open an administrative port to 0.0.0.0/0.",
    ],
    Kind.LOAD_BALANCER: [
        "Terminate TLS at the balancer and redirect plain HTTP to it.",
        "Enable access logs before you need them.",
    ],
    Kind.NAT_GATEWAY: [
        "One per availability zone for zonal resilience; one in total if cost "
        "matters more.",
        "Add S3 and DynamoDB gateway endpoints to keep that traffic off it.",
    ],
    Kind.IAM_ROLE: [
        "Scope the policy to the resources actually used.",
        "Prefer a role per workload over one role shared by several.",
    ],
    Kind.KUBERNETES_CLUSTER: [
        "Run nodes in private subnets.",
        "Use IAM roles for service accounts rather than node-wide permissions.",
    ],
    Kind.FUNCTION: [
        "Set a timeout and a memory size deliberately; the defaults suit "
        "almost nothing.",
        "Send failures to a dead-letter queue.",
    ],
    Kind.CACHE: [
        "Treat the cache as disposable. An application that cannot survive a "
        "cold cache is not correct.",
    ],
    Kind.MONITORING: [
        "Alarm on symptoms users feel, not on every metric available.",
    ],
}

_BLOCK_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _snippet(terraform: dict[str, str], resource_id: str) -> tuple[str | None, str | None]:
    """The HCL block for one resource, and the file it lives in.

    Extracted server-side so the API, the reports and the assistant all quote
    exactly the same text the viewer shows.
    """
    pattern = _BLOCK_RE_CACHE.get(resource_id)
    if pattern is None:
        pattern = re.compile(
            rf'^(?:resource|data)\s+"[\w-]+"\s+"{re.escape(resource_id)}"[\s\S]*?^\}}',
            re.MULTILINE,
        )
        _BLOCK_RE_CACHE[resource_id] = pattern

    for filename, content in terraform.items():
        if not filename.endswith(".tf"):
            continue
        match = pattern.search(content)
        if match:
            return match.group(0), filename
    return None, None


@dataclass
class Explanation:
    """Everything deterministically known about one resource."""

    resource_id: str
    name: str
    kind: str

    # -- provenance ------------------------------------------------------
    requested: bool
    origin: str
    reason: str
    rule: str | None = None
    triggered_by: str | None = None
    evidence: str | None = None
    confidence: float = 1.0
    external_id: str | None = None

    # -- relationships ---------------------------------------------------
    depends_on: list[str] = field(default_factory=list)
    required_by: list[str] = field(default_factory=list)

    # -- money -----------------------------------------------------------
    monthly_cost_usd: float = 0.0

    # -- code ------------------------------------------------------------
    terraform_snippet: str | None = None
    terraform_file: str | None = None

    # -- service knowledge -----------------------------------------------
    pillar: str = ""
    security_notes: str | None = None
    networking_notes: str | None = None
    operational_notes: str | None = None
    alternatives: list[dict[str, str]] = field(default_factory=list)
    best_practices: list[str] = field(default_factory=list)

    # -- this design's findings -------------------------------------------
    finding_codes: list[str] = field(default_factory=list)
    recommendation_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Explainer:
    """Builds an :class:`Explanation` for every resource in a design."""

    def explain_all(
        self,
        spec: InfrastructureSpec,
        terraform: dict[str, str] | None = None,
        findings: list | None = None,
        recommendations: list | None = None,
    ) -> list[Explanation]:
        terraform = terraform or {}
        graph = spec.dependency_graph()

        # Inverted once rather than per resource: the graph is small, but the
        # inversion is the same for every explanation built from it.
        dependents: dict[str, list[str]] = {}
        for child, parents in graph.items():
            for parent in parents:
                dependents.setdefault(parent, []).append(child)

        findings_by_resource: dict[str, list[str]] = {}
        for finding in findings or []:
            target = getattr(finding, "resource_id", None)
            if target:
                findings_by_resource.setdefault(target, []).append(finding.code)

        recs_by_resource: dict[str, list[str]] = {}
        for rec in recommendations or []:
            for target in getattr(rec, "resources", []):
                recs_by_resource.setdefault(target, []).append(rec.id)

        return [
            self._explain(
                resource, spec, terraform, graph, dependents,
                findings_by_resource, recs_by_resource,
            )
            for resource in spec.resources
        ]

    def explain(
        self,
        spec: InfrastructureSpec,
        resource_id: str,
        terraform: dict[str, str] | None = None,
        findings: list | None = None,
        recommendations: list | None = None,
    ) -> Explanation | None:
        for item in self.explain_all(spec, terraform, findings, recommendations):
            if item.resource_id == resource_id:
                return item
        return None

    # ------------------------------------------------------------------

    @staticmethod
    def _explain(
        resource: Resource,
        spec: InfrastructureSpec,
        terraform: dict[str, str],
        graph: dict[str, list[str]],
        dependents: dict[str, list[str]],
        findings_by_resource: dict[str, list[str]],
        recs_by_resource: dict[str, list[str]],
    ) -> Explanation:
        snippet, filename = _snippet(terraform, resource.id)
        unit = MONTHLY_COST_HINTS.get(resource.kind, 0.0)

        return Explanation(
            resource_id=resource.id,
            name=resource.name,
            kind=resource.kind.value,
            requested=resource.origin is Origin.EXPLICIT,
            origin=resource.origin.value,
            reason=resource.reason,
            rule=resource.rule,
            triggered_by=resource.triggered_by,
            evidence=resource.evidence,
            confidence=resource.confidence,
            external_id=resource.external_id,
            depends_on=sorted(graph.get(resource.id, [])),
            required_by=sorted(dependents.get(resource.id, [])),
            # An existing resource is not created, so it costs this project
            # nothing even though the service itself is billed to someone.
            monthly_cost_usd=0.0 if resource.is_external else unit * resource.count,
            terraform_snippet=snippet,
            terraform_file=filename,
            pillar=PILLAR.get(resource.kind, "Operational Excellence"),
            security_notes=SECURITY_NOTES.get(resource.kind),
            networking_notes=NETWORKING_NOTES.get(resource.kind),
            operational_notes=OPERATIONAL_NOTES.get(resource.kind),
            alternatives=ALTERNATIVES.get(resource.kind, []),
            best_practices=BEST_PRACTICES.get(resource.kind, []),
            finding_codes=sorted(findings_by_resource.get(resource.id, [])),
            recommendation_ids=sorted(recs_by_resource.get(resource.id, [])),
        )
