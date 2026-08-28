"""Specification validation and policy checks.

Runs after mapping, over the completed graph.  Two jobs:

* **Structural validation** -- catch anything the generators cannot render
  (dangling edges, duplicate ids, orphaned resources).
* **Policy checks** -- the "automated checks against security policy" and
  cost hints listed as future work in the paper, implemented as a small
  rule set over the IR.

Findings never block generation; they are attached to the response so the user
sees them next to the code they are about to run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from app.models.ir import EdgeKind, InfrastructureSpec, Kind

Severity = Literal["error", "warning", "info"]

# Very rough on-demand monthly USD estimates, used only for relative guidance.
MONTHLY_COST_HINTS: dict[Kind, float] = {
    Kind.VM: 8.0,
    Kind.AUTOSCALING_GROUP: 24.0,
    Kind.NAT_GATEWAY: 33.0,
    Kind.LOAD_BALANCER: 18.0,
    Kind.SQL_DATABASE: 25.0,
    Kind.CACHE: 13.0,
    Kind.KUBERNETES_CLUSTER: 73.0,
    Kind.CONTAINER_SERVICE: 15.0,
    Kind.OBJECT_STORAGE: 1.0,
    Kind.CDN: 5.0,
    Kind.FUNCTION: 1.0,
    Kind.NOSQL_TABLE: 2.0,
    Kind.DATA_WAREHOUSE: 180.0,
}


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str
    resource_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class SpecValidator:
    """Structural + policy validation over a mapped specification."""

    def validate(self, spec: InfrastructureSpec) -> list[Finding]:
        findings: list[Finding] = []
        findings += self._structural(spec)
        findings += self._security(spec)
        findings += self._reliability(spec)
        findings += self._cost(spec)
        return findings

    # -- structural --------------------------------------------------------

    def _structural(self, spec: InfrastructureSpec) -> list[Finding]:
        out: list[Finding] = []
        seen: set[str] = set()
        for r in spec.resources:
            if r.id in seen:
                out.append(Finding("error", "duplicate_id",
                                   f"Duplicate resource id {r.id!r}.", r.id))
            seen.add(r.id)

        for e in spec.edges:
            if e.source not in seen:
                out.append(Finding("error", "dangling_edge",
                                   f"Edge references unknown resource {e.source!r}."))
            if e.target not in seen:
                out.append(Finding("error", "dangling_edge",
                                   f"Edge references unknown resource {e.target!r}."))

        connected = {e.source for e in spec.edges} | {e.target for e in spec.edges}
        # Structural and cross-cutting resources legitimately have no edges of
        # their own -- route tables belong to a subnet band, not to a flow.
        standalone = {Kind.OBJECT_STORAGE, Kind.DNS_ZONE, Kind.MONITORING,
                      Kind.KEY_MANAGEMENT, Kind.CONTAINER_REGISTRY, Kind.NOSQL_TABLE,
                      Kind.ROUTE_TABLE, Kind.SUBNET_PUBLIC, Kind.SUBNET_PRIVATE,
                      Kind.VPC, Kind.INTERNET_GATEWAY}
        for r in spec.resources:
            if r.id not in connected and r.kind not in standalone and len(spec.resources) > 1:
                out.append(Finding(
                    "info", "orphan_resource",
                    f"{r.name} is not connected to anything else in the design.", r.id,
                ))
        return out

    # -- security ----------------------------------------------------------

    def _security(self, spec: InfrastructureSpec) -> list[Finding]:
        out: list[Finding] = []

        for sg in spec.of_kind(Kind.SECURITY_GROUP):
            if sg.properties.get("ingress_from") == "0.0.0.0/0":
                ports = sg.properties.get("ingress_ports", [])
                if 22 in ports or 3389 in ports:
                    out.append(Finding(
                        "error", "open_admin_port",
                        f"{sg.name} exposes administrative access "
                        f"(port {'22' if 22 in ports else '3389'}) to the whole internet.",
                        sg.id,
                    ))
                elif sg.properties.get("purpose") == "application":
                    out.append(Finding(
                        "warning", "open_app_port",
                        f"{sg.name} allows traffic from any address; put the workload "
                        "behind a load balancer or restrict the CIDR.", sg.id,
                    ))

        db = spec.first(Kind.SQL_DATABASE)
        if db is not None:
            exposed = (db.properties.get("subnet_band") == "public"
                       or db.properties.get("publicly_accessible"))
            if exposed:
                out.append(Finding("error", "public_database",
                                   "The database is placed in a public subnet.", db.id))
            if not db.properties.get("storage_encrypted", True):
                out.append(Finding("warning", "unencrypted_storage",
                                   "Database storage encryption is disabled.", db.id))

        for bucket in spec.of_kind(Kind.OBJECT_STORAGE):
            if bucket.properties.get("public_read"):
                out.append(Finding(
                    "warning", "public_bucket",
                    f"{bucket.name} is configured for public read access. Serve it "
                    "through CloudFront with origin access control instead.", bucket.id,
                ))

        if spec.environment == "prod" and not spec.has(Kind.SECRET_STORE) and spec.has(
            Kind.SQL_DATABASE
        ):
            out.append(Finding("warning", "no_secret_store",
                               "Production database without a managed secret store."))
        return out

    # -- reliability -------------------------------------------------------

    def _reliability(self, spec: InfrastructureSpec) -> list[Finding]:
        out: list[Finding] = []
        if spec.environment == "prod":
            if not spec.high_availability:
                out.append(Finding(
                    "warning", "prod_single_az",
                    "Production workload is not marked high availability; "
                    "resources may be single-AZ.",
                ))
            db = spec.first(Kind.SQL_DATABASE)
            if db is not None and not db.properties.get("multi_az"):
                out.append(Finding("warning", "db_single_az",
                                   "Production database is not Multi-AZ.", db.id))
            for vm in spec.of_kind(Kind.VM):
                if vm.count == 1 and not spec.has(Kind.AUTOSCALING_GROUP):
                    out.append(Finding(
                        "warning", "single_instance",
                        f"{vm.name} is a single instance with no auto scaling group; "
                        "it is a single point of failure.", vm.id,
                    ))

        lb = spec.first(Kind.LOAD_BALANCER)
        if lb is not None:
            forwards = any(
                e.source == lb.id and e.kind is EdgeKind.TRAFFIC for e in spec.edges
            )
            if not forwards:
                out.append(Finding("warning", "lb_no_targets",
                                   "The load balancer has no downstream targets.", lb.id))
        return out

    # -- cost --------------------------------------------------------------

    def _cost(self, spec: InfrastructureSpec) -> list[Finding]:
        out: list[Finding] = []
        nat = spec.first(Kind.NAT_GATEWAY)
        if nat is not None and nat.count > 1:
            out.append(Finding(
                "info", "nat_cost",
                f"{nat.count} NAT gateways cost roughly "
                f"${MONTHLY_COST_HINTS[Kind.NAT_GATEWAY] * nat.count:.0f}/month before data "
                "transfer. One NAT gateway is cheaper if you can accept AZ-level risk.",
                nat.id,
            ))
        if spec.environment != "prod":
            for r in spec.resources:
                size = str(r.properties.get("instance_type", ""))
                if size and not size.startswith(("t2.", "t3.", "t4g.")):
                    out.append(Finding(
                        "info", "oversized_nonprod",
                        f"{r.name} uses {size} in a {spec.environment} environment; "
                        "a burstable t-family size is usually enough.", r.id,
                    ))
        return out


def estimate_monthly_cost(spec: InfrastructureSpec) -> float:
    """Rough order-of-magnitude monthly cost, for the UI summary only."""
    total = 0.0
    for r in spec.resources:
        total += MONTHLY_COST_HINTS.get(r.kind, 0.0) * r.count
    return round(total, 2)
