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

from app.engine.constraints import check as check_constraints
from app.engine.policy import PRIVATE_EGRESS_CONSUMERS
from app.models.ir import EdgeKind, InfrastructureSpec, Kind

Severity = Literal["error", "warning", "info"]

#: Compute that AWS will not let you grant permissions to without a role.
NEEDS_ROLE: frozenset[Kind] = frozenset({
    Kind.VM, Kind.AUTOSCALING_GROUP, Kind.FUNCTION, Kind.CONTAINER_SERVICE,
    Kind.KUBERNETES_CLUSTER,
})

# Very rough on-demand monthly USD estimates, used only for relative guidance.
MONTHLY_COST_HINTS: dict[Kind, float] = {
    Kind.VM: 8.0,
    Kind.AUTOSCALING_GROUP: 24.0,
    Kind.NAT_GATEWAY: 33.0,
    Kind.LOAD_BALANCER: 18.0,
    Kind.SQL_DATABASE: 25.0,
    Kind.SQL_CLUSTER: 90.0,
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
        findings += self._network(spec)
        findings += self._security(spec)
        findings += self._reliability(spec)
        findings += self._cost(spec)
        findings += self._aws_constraints(spec)
        return findings

    # -- AWS API constraints ----------------------------------------------

    def _aws_constraints(self, spec: InfrastructureSpec) -> list[Finding]:
        """Rules the AWS API enforces that `terraform validate` cannot see."""
        return [
            Finding(severity, code, message)  # type: ignore[arg-type]
            for severity, code, message in check_constraints(spec)
        ]

    # -- network correctness ----------------------------------------------

    def _network(self, spec: InfrastructureSpec) -> list[Finding]:
        """Checks that a deployment would actually fail, or silently misbehave."""
        out: list[Finding] = []

        # A public subnet without an internet gateway is not public.
        if spec.has(Kind.SUBNET_PUBLIC) and not spec.has(Kind.INTERNET_GATEWAY):
            out.append(Finding(
                "error", "missing_internet_gateway",
                "A public subnet exists but there is no internet gateway, so "
                "nothing in it can reach the internet.",
            ))

        # Private compute with no NAT cannot install packages or call APIs.
        private_compute = [
            r for r in spec.resources
            if r.kind in PRIVATE_EGRESS_CONSUMERS
            and r.properties.get("subnet_band") == "private"
        ]
        if private_compute and not spec.has(Kind.NAT_GATEWAY):
            names = ", ".join(r.name for r in private_compute)
            out.append(Finding(
                "warning", "private_subnet_without_nat",
                f"{names} sits in a private subnet with no NAT gateway, so it "
                "has no outbound internet access.",
                private_compute[0].id,
            ))

        # A route table nothing routes through is dead configuration.
        for rt in spec.of_kind(Kind.ROUTE_TABLE):
            scope = rt.properties.get("scope")
            subnet_kind = Kind.SUBNET_PUBLIC if scope == "public" else Kind.SUBNET_PRIVATE
            if not spec.has(subnet_kind):
                out.append(Finding(
                    "warning", "unattached_route_table",
                    f"{rt.name} has no {scope} subnet to associate with.", rt.id,
                ))

        # Two networks claiming the same address range will not route.
        seen_cidrs: dict[str, str] = {}
        for r in spec.resources:
            cidr = r.properties.get("cidr_block")
            if not cidr:
                continue
            if cidr in seen_cidrs:
                out.append(Finding(
                    "error", "duplicate_cidr",
                    f"{r.name} and {seen_cidrs[cidr]} both use {cidr}.", r.id,
                ))
            seen_cidrs[str(cidr)] = r.name

        # Alarms with nothing to watch produce an empty monitoring stack.
        if spec.has(Kind.MONITORING) and not spec.of_kind(
            Kind.AUTOSCALING_GROUP, Kind.SQL_DATABASE, Kind.VM
        ):
            out.append(Finding(
                "warning", "monitoring_no_targets",
                "Monitoring was requested but there is no compute or database "
                "to raise alarms on.",
                spec.first(Kind.MONITORING).id,
            ))

        # Terraform will not converge on a dependency cycle.
        for cycle in spec.find_cycles():
            out.append(Finding(
                "error", "circular_dependency",
                "Circular dependency: " + " -> ".join(cycle) + ".",
                cycle[0],
            ))

        return out

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

        # A security group attached to nothing is a leftover. Matched on edge
        # structure rather than on the "protects" label, because a group that
        # is also a declared dependency carries the dependency label instead.
        attached = {
            e.source for e in spec.edges if e.kind is EdgeKind.DEPENDENCY
        }
        for sg in spec.of_kind(Kind.SECURITY_GROUP):
            if sg.id not in attached:
                out.append(Finding(
                    "info", "unused_security_group",
                    f"{sg.name} is not attached to any resource.", sg.id,
                ))

        # Compute without an identity cannot be given permissions later.
        role_ids = {r.id for r in spec.of_kind(Kind.IAM_ROLE)}
        for compute in spec.resources:
            if compute.kind not in NEEDS_ROLE:
                continue
            has_role = any(
                e.target == compute.id
                and e.source in role_ids
                and e.kind is EdgeKind.DEPENDENCY
                for e in spec.edges
            )
            if not has_role:
                out.append(Finding(
                    "warning", "missing_iam_role",
                    f"{compute.name} has no IAM role attached.", compute.id,
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
