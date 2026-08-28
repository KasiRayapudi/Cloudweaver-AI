"""Resource mapping engine (paper section VI, step 3).

The extractor reports what the user asked for.  This module works out the
minimum set of additional resources without which those cannot be deployed,
and wires the graph together.

The rule it enforces, and the only one:

    a resource exists because the user asked for it, or because something the
    user asked for cannot be deployed without it.

The dependency rules themselves live in :mod:`app.engine.policy` as data.
This module is a fixed-point loop over that table plus the edge wiring, so it
holds no opinion about any particular service.  That separation is what makes
"never invent a resource" a property of the engine rather than a promise about
its control flow.
"""

from __future__ import annotations

from app.engine.policy import (
    REQUIREMENTS,
    Requirement,
    requirements_for,
)
from app.models.ir import (
    EdgeKind,
    InfrastructureSpec,
    Kind,
    Origin,
    Resource,
)
from app.nlp.catalog import service_for

MAX_CLOSURE_PASSES = 12

# Default identifier used when a requirement creates a resource.
CANONICAL_IDS: dict[Kind, str] = {
    Kind.VPC: "main",
    Kind.SUBNET_PUBLIC: "public",
    Kind.SUBNET_PRIVATE: "private",
    Kind.INTERNET_GATEWAY: "igw",
    Kind.NAT_GATEWAY: "nat",
    Kind.ROUTE_TABLE: "route_table",
    Kind.SECURITY_GROUP: "app_sg",
    Kind.ELASTIC_IP: "eip",
    Kind.TARGET_GROUP: "app_tg",
    Kind.IAM_ROLE: "instance_role",
}

# Compute that runs application code.
COMPUTE: frozenset[Kind] = frozenset({
    Kind.VM, Kind.AUTOSCALING_GROUP, Kind.CONTAINER_SERVICE, Kind.KUBERNETES_CLUSTER,
    Kind.FUNCTION,
})

# Backends application compute talks to, and the verb for the diagram edge.
BACKEND_LABELS: dict[Kind, str] = {
    Kind.SQL_DATABASE: "queries",
    Kind.SQL_CLUSTER: "queries",
    Kind.NOSQL_TABLE: "reads/writes",
    Kind.CACHE: "caches",
    Kind.OBJECT_STORAGE: "objects",
    Kind.QUEUE: "enqueues",
    Kind.TOPIC: "publishes",
    Kind.FILE_STORAGE: "mounts",
    Kind.SECRET_STORE: "reads",
    Kind.DATA_WAREHOUSE: "loads",
}

# Compute in the order a load balancer should prefer as its target.
COMPUTE_ORDER: tuple[Kind, ...] = (
    Kind.AUTOSCALING_GROUP, Kind.CONTAINER_SERVICE, Kind.KUBERNETES_CLUSTER,
    Kind.VM, Kind.FUNCTION,
)

# Resources that belong in a public subnet when a VPC exists.
PUBLIC_BAND: frozenset[Kind] = frozenset({
    Kind.LOAD_BALANCER, Kind.BASTION, Kind.NAT_GATEWAY,
})

# Resources that must never sit in a public subnet.
PRIVATE_BAND: frozenset[Kind] = frozenset({
    Kind.SQL_DATABASE, Kind.SQL_CLUSTER, Kind.CACHE, Kind.DATA_WAREHOUSE,
})

# Default ingress ports per security group purpose.
DEFAULT_PORTS: dict[str, list[int]] = {
    "load-balancer": [80, 443],
    "application": [80, 443],
    "database": [5432],
    "cache": [6379],
    "bastion": [22],
    "filesystem": [2049],
}


class ResourceMapper:
    """Completes a draft spec into the minimum deployable resource graph."""

    def map(self, spec: InfrastructureSpec) -> InfrastructureSpec:
        if not spec.resources:
            return spec

        self._close_over_requirements(spec)
        self._place_in_subnets(spec)
        self._configure_network(spec)
        self._configure_security_groups(spec)
        self._wire_edges(spec)
        return spec

    # ------------------------------------------------------------------
    # stage 1: mandatory dependency closure
    # ------------------------------------------------------------------

    def _close_over_requirements(self, spec: InfrastructureSpec) -> None:
        """Add mandatory dependencies until the set stops growing.

        Iterative rather than recursive because requirements are conditional:
        adding a private subnet can make a NAT gateway mandatory, which makes a
        public subnet mandatory, and so on. The loop settles when a full pass
        adds nothing.
        """
        for _ in range(MAX_CLOSURE_PASSES):
            added = False
            # Snapshot: creating resources during iteration would skip entries.
            for resource in list(spec.resources):
                for requirement in requirements_for(resource.kind):
                    if not requirement.applies(spec):
                        continue
                    if requirement.satisfied_by(spec):
                        self._merge_properties(spec, requirement)
                        dependency = self._resolve(spec, requirement)
                    else:
                        dependency = self._create(spec, requirement, because=resource)
                        added = True
                    # Record the real creation-order constraint, so the
                    # dependency graph reflects the policy rather than being
                    # inferred from whatever the diagram happens to draw.
                    if dependency is not None:
                        spec.connect(
                            dependency.id, resource.id,
                            EdgeKind.DEPENDENCY, "required by",
                        )
            if not added:
                return
        spec.warn(
            "Dependency resolution did not settle; the design may be incomplete."
        )

    def _create(
        self, spec: InfrastructureSpec, requirement: Requirement, because: Resource
    ) -> Resource:
        info = service_for(requirement.kind, spec.provider)
        resource_id = requirement.id_hint or CANONICAL_IDS.get(
            requirement.kind, requirement.kind.value
        )

        resource = Resource(
            id=resource_id,
            kind=requirement.kind,
            name=info.display,
            tier=info.tier,
            origin=Origin.REQUIRED,
            properties=dict(requirement.properties),
            confidence=1.0,
            reason=requirement.reason.format(name=because.name),
        )
        spec.resources.append(resource)
        spec.note(f"{resource.name}: {resource.reason}")
        return resource

    @staticmethod
    def _resolve(spec: InfrastructureSpec, requirement: Requirement) -> Resource | None:
        """The existing resource that satisfies this requirement."""
        if requirement.id_hint is not None:
            return spec.get(requirement.id_hint)
        return spec.first(requirement.kind)

    @staticmethod
    def _merge_properties(spec: InfrastructureSpec, requirement: Requirement) -> None:
        """Seed a requirement's properties onto a resource that already exists.

        This is how a security group the user asked for by name acquires the
        purpose of the tier it protects, instead of a second one being created
        alongside it.
        """
        if not requirement.properties:
            return
        existing = (
            spec.get(requirement.id_hint) if requirement.id_hint
            else spec.first(requirement.kind)
        )
        if existing is not None:
            for key, value in requirement.properties.items():
                existing.properties.setdefault(key, value)

    # ------------------------------------------------------------------
    # stage 2: subnet placement
    # ------------------------------------------------------------------

    def _place_in_subnets(self, spec: InfrastructureSpec) -> None:
        """Record which subnet band each VPC resource belongs to."""
        if not spec.has(Kind.VPC):
            return
        private_intent = spec.private_placement_requested

        for resource in spec.resources:
            if resource.kind in PUBLIC_BAND:
                resource.properties["subnet_band"] = "public"
            elif resource.kind in PRIVATE_BAND:
                resource.properties["subnet_band"] = "private"
            elif resource.kind in COMPUTE and resource.kind is not Kind.FUNCTION:
                # Compute follows stated intent only. The mere existence of a
                # private subnet -- which a database creates for itself -- is
                # not a reason to move a public web server behind a NAT.
                resource.properties.setdefault(
                    "subnet_band", "private" if private_intent else "public"
                )

    # ------------------------------------------------------------------
    # stage 3: network configuration
    # ------------------------------------------------------------------

    def _configure_network(self, spec: InfrastructureSpec) -> None:
        vpc = spec.first(Kind.VPC)
        if vpc is None:
            return

        if not vpc.is_external:
            # An existing VPC already has a range; choosing one for it would
            # be describing infrastructure we do not control.
            vpc.properties.setdefault("cidr_block", "10.0.0.0/16")
            vpc.properties.setdefault("enable_dns_hostnames", True)

        azs = spec.availability_zones
        for subnet in spec.of_kind(Kind.SUBNET_PUBLIC):
            subnet.count = azs
            subnet.properties.update({"map_public_ip": True, "offset": 0})
        for subnet in spec.of_kind(Kind.SUBNET_PRIVATE):
            subnet.count = azs
            subnet.properties.update({"map_public_ip": False, "offset": 10})

        nat = spec.first(Kind.NAT_GATEWAY)
        if nat is not None:
            nat.count = azs if spec.high_availability else 1
            if nat.count > 1:
                spec.note(
                    f"One NAT gateway per availability zone ({nat.count} total) "
                    "because high availability was requested."
                )

        # An Elastic IP created for a NAT gateway scales with it; one attached
        # to an instance does not.
        eip = spec.first(Kind.ELASTIC_IP)
        if eip is not None and eip.origin is Origin.REQUIRED and nat is not None:
            eip.count = nat.count
            eip.properties.setdefault("attached_to", nat.id)
        elif eip is not None:
            target = self._primary_compute(spec)
            if target is not None:
                eip.properties.setdefault("attached_to", target.id)

    # ------------------------------------------------------------------
    # stage 4: security groups
    # ------------------------------------------------------------------

    def _configure_security_groups(self, spec: InfrastructureSpec) -> None:
        """Give each security group a purpose and a least-privilege ingress.

        Only groups that already exist are configured. The closure decides how
        many there are; this decides what they allow.
        """
        groups = spec.of_kind(Kind.SECURITY_GROUP)
        if not groups:
            return

        lb = spec.first(Kind.LOAD_BALANCER)
        app = self._primary_compute(spec)

        # The closure already created exactly one group per consumer, with the
        # id and purpose recorded in the policy table, so this only has to fill
        # in what each one allows.
        protected_by: dict[str, Resource | None] = {
            "alb_sg": lb,
            "app_sg": app,
            "db_sg": spec.first(Kind.SQL_DATABASE) or spec.first(Kind.SQL_CLUSTER),
            "cache_sg": spec.first(Kind.CACHE),
            "bastion_sg": spec.first(Kind.BASTION),
            "warehouse_sg": spec.first(Kind.DATA_WAREHOUSE),
        }

        for group in groups:
            purpose = str(group.properties.get("purpose") or "application")
            self._configure_group(
                spec, group, purpose, protected_by.get(group.id), lb, app
            )

    def _configure_group(
        self,
        spec: InfrastructureSpec,
        group: Resource,
        purpose: str,
        protects: Resource | None,
        lb: Resource | None,
        app: Resource | None,
    ) -> None:
        props = group.properties
        props["purpose"] = purpose
        group.name = {
            "load-balancer": "ALB Security Group",
            "application": "App Security Group",
            "database": "Database Security Group",
            "cache": "Cache Security Group",
            "bastion": "Bastion Security Group",
        }.get(purpose, group.name)

        # Ports the user named win over any default.
        if not props.get("ingress_ports"):
            ports = list(DEFAULT_PORTS.get(purpose, [443]))
            database = spec.first(Kind.SQL_DATABASE) or spec.first(Kind.SQL_CLUSTER)
            if purpose == "database" and database is not None:
                engine = str(database.properties.get("engine", ""))
                mysql_family = ("mysql", "aurora-mysql", "mariadb")
                ports = [3306] if engine.startswith(mysql_family) else [5432]
            props["ingress_ports"] = ports

        if not props.get("ingress_from"):
            if purpose == "load-balancer":
                props["ingress_from"] = "0.0.0.0/0"
            elif purpose == "application":
                props["ingress_from"] = "alb_sg" if lb is not None else "0.0.0.0/0"
            elif purpose == "bastion":
                props["ingress_from"] = "0.0.0.0/0"
            else:
                props["ingress_from"] = "app_sg" if app is not None else "vpc"

        if protects is not None:
            spec.connect(group.id, protects.id, EdgeKind.DEPENDENCY, "protects")

        # Database ports follow the engine even when the group was reused.
        if purpose == "database":
            for db in spec.of_kind(Kind.SQL_DATABASE, Kind.SQL_CLUSTER):
                db.properties.setdefault("port", props["ingress_ports"][0])

    # ------------------------------------------------------------------
    # stage 5: edges
    # ------------------------------------------------------------------

    def _wire_edges(self, spec: InfrastructureSpec) -> None:
        self._network_edges(spec)
        self._traffic_edges(spec)
        self._data_edges(spec)
        self._identity_edges(spec)

    def _network_edges(self, spec: InfrastructureSpec) -> None:
        vpc = spec.first(Kind.VPC)
        if vpc is None:
            return
        for subnet in spec.of_kind(Kind.SUBNET_PUBLIC, Kind.SUBNET_PRIVATE):
            spec.connect(vpc.id, subnet.id, EdgeKind.CONTAINMENT)
        igw = spec.first(Kind.INTERNET_GATEWAY)
        if igw is not None:
            # Container -> contained, always. Pointing this the other way round
            # ("the gateway is attached to the VPC") reads naturally but makes
            # the creation-order graph cyclic, since the VPC must exist first.
            spec.connect(vpc.id, igw.id, EdgeKind.CONTAINMENT, "attached to")
        nat = spec.first(Kind.NAT_GATEWAY)
        if nat is not None and igw is not None:
            spec.connect(nat.id, igw.id, EdgeKind.TRAFFIC, "egress")
        for rt in spec.of_kind(Kind.ROUTE_TABLE):
            spec.connect(vpc.id, rt.id, EdgeKind.CONTAINMENT)
        eip = spec.first(Kind.ELASTIC_IP)
        if eip is not None:
            attached = spec.get(str(eip.properties.get("attached_to", "")))
            if attached is not None:
                spec.connect(eip.id, attached.id, EdgeKind.DEPENDENCY, "public IP")

    def _traffic_edges(self, spec: InfrastructureSpec) -> None:
        lb = spec.first(Kind.LOAD_BALANCER)
        tg = spec.first(Kind.TARGET_GROUP)
        target = self._primary_compute(spec, exclude={Kind.FUNCTION})

        if lb is not None and tg is not None:
            spec.connect(lb.id, tg.id, EdgeKind.TRAFFIC, "forwards")
            lb.properties.setdefault("scheme", "internet-facing")
            lb.properties.setdefault("listener_port", 80)
            tg.properties.setdefault("port", 80)
            tg.properties.setdefault("health_check_path", "/health")
            if target is not None:
                spec.connect(tg.id, target.id, EdgeKind.TRAFFIC, "targets")
            else:
                spec.warn("The load balancer has no compute to route to.")

        api = spec.first(Kind.API_GATEWAY)
        fn = spec.first(Kind.FUNCTION)
        if api is not None:
            if fn is not None:
                api.properties.setdefault("integration", "lambda")
                spec.connect(api.id, fn.id, EdgeKind.TRAFFIC, "invokes")
            elif lb is not None:
                api.properties.setdefault("integration", "http_proxy")
                spec.connect(api.id, lb.id, EdgeKind.TRAFFIC, "proxies")
            else:
                spec.warn("API Gateway was requested but has no backend to integrate with.")

        cdn = spec.first(Kind.CDN)
        if cdn is not None:
            bucket = spec.first(Kind.OBJECT_STORAGE)
            if bucket is not None:
                cdn.properties.setdefault("origin", bucket.id)
                cdn.properties.setdefault("origin_type", "s3")
                spec.connect(cdn.id, bucket.id, EdgeKind.TRAFFIC, "origin")
                if bucket.properties.get("public_read"):
                    bucket.properties["public_read"] = False
                    spec.note(
                        "The bucket is kept private and served through CloudFront "
                        "using origin access control."
                    )
            elif lb is not None:
                cdn.properties.setdefault("origin", lb.id)
                cdn.properties.setdefault("origin_type", "alb")
                spec.connect(cdn.id, lb.id, EdgeKind.TRAFFIC, "origin")
            else:
                spec.warn("CloudFront was requested but has no origin to serve.")

        waf = spec.first(Kind.WAF)
        if waf is not None:
            attach = cdn or api or lb
            if attach is not None:
                waf.properties.setdefault(
                    "scope", "CLOUDFRONT" if attach is cdn else "REGIONAL"
                )
                spec.connect(waf.id, attach.id, EdgeKind.DEPENDENCY, "inspects")

        dns = spec.first(Kind.DNS_ZONE)
        if dns is not None:
            entry = cdn or api or lb
            if entry is not None:
                dns.properties.setdefault("alias_target", entry.id)
                spec.connect(dns.id, entry.id, EdgeKind.TRAFFIC, "alias")

        queue = spec.first(Kind.QUEUE)
        if queue is not None and fn is not None:
            spec.connect(queue.id, fn.id, EdgeKind.TRAFFIC, "triggers")

        bastion = spec.first(Kind.BASTION)
        if bastion is not None and target is not None:
            spec.connect(bastion.id, target.id, EdgeKind.DEPENDENCY, "admin access")

        registry = spec.first(Kind.CONTAINER_REGISTRY)
        if registry is not None:
            for runner in spec.of_kind(Kind.CONTAINER_SERVICE, Kind.KUBERNETES_CLUSTER):
                spec.connect(registry.id, runner.id, EdgeKind.DEPENDENCY, "images")

    def _data_edges(self, spec: InfrastructureSpec) -> None:
        compute = [r for r in spec.resources if r.kind in COMPUTE]
        backends = [r for r in spec.resources if r.kind in BACKEND_LABELS]
        for c in compute:
            for b in backends:
                spec.connect(c.id, b.id, EdgeKind.DATA, BACKEND_LABELS[b.kind])

    def _identity_edges(self, spec: InfrastructureSpec) -> None:
        """Attach each IAM role to the compute that assumes it.

        One role per compute family, per the rule that roles are not invented:
        an EC2 workload gets an instance role, a Lambda gets an execution role,
        and nothing else is created.
        """
        roles = spec.of_kind(Kind.IAM_ROLE)
        if not roles:
            return

        # Which compute each role id serves. The closure created one role per
        # family that is actually present, so there is nothing to invent here.
        families: dict[str, tuple[frozenset[Kind], str]] = {
            "instance_role": (
                frozenset({Kind.VM, Kind.AUTOSCALING_GROUP, Kind.BASTION}),
                "EC2 Instance Role",
            ),
            "lambda_role": (frozenset({Kind.FUNCTION}), "Lambda Execution Role"),
            "task_role": (frozenset({Kind.CONTAINER_SERVICE}), "ECS Task Execution Role"),
            "cluster_role": (frozenset({Kind.KUBERNETES_CLUSTER}), "EKS Cluster Role"),
        }

        for role in roles:
            kinds, role_name = families.get(role.id, (frozenset(), role.name))
            role.name = role_name
            role.properties.setdefault("service", "ec2.amazonaws.com")
            for consumer in (r for r in spec.resources if r.kind in kinds):
                spec.connect(role.id, consumer.id, EdgeKind.DEPENDENCY, "assumed by")

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------

    @staticmethod
    def _primary_compute(
        spec: InfrastructureSpec, exclude: frozenset[Kind] | set[Kind] = frozenset()
    ) -> Resource | None:
        """The compute resource that fronts the application, if any."""
        for kind in COMPUTE_ORDER:
            if kind in exclude:
                continue
            found = spec.first(kind)
            if found is not None:
                return found
        return None


__all__ = ["ResourceMapper", "COMPUTE", "COMPUTE_ORDER", "REQUIREMENTS"]
