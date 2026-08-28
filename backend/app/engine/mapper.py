"""Resource mapping engine (paper section VI, step 3).

The extractor reports what the user asked for.  This module works out what
that actually requires on the target cloud: a VPC to live in, subnets split
across availability zones, gateways for egress, security groups with the right
allow-rules, target groups, IAM roles -- plus every edge between them.

All of it is deterministic, and all of it happens *before* either generator
runs.  That ordering is the reason the diagram and the Terraform never
disagree: they read the same completed graph.
"""

from __future__ import annotations

from app.models.ir import (
    EdgeKind,
    InfrastructureSpec,
    Kind,
    Origin,
    Resource,
    slugify,
)
from app.nlp.catalog import service_for

# Resources that must sit inside a VPC.
VPC_SCOPED: frozenset[Kind] = frozenset({
    Kind.VM, Kind.AUTOSCALING_GROUP, Kind.CONTAINER_SERVICE, Kind.KUBERNETES_CLUSTER,
    Kind.LOAD_BALANCER, Kind.SQL_DATABASE, Kind.CACHE, Kind.BASTION, Kind.FILE_STORAGE,
    Kind.DATA_WAREHOUSE,
})

# Compute that runs application code and therefore may need egress + backends.
COMPUTE: frozenset[Kind] = frozenset({
    Kind.VM, Kind.AUTOSCALING_GROUP, Kind.CONTAINER_SERVICE, Kind.KUBERNETES_CLUSTER,
    Kind.FUNCTION,
})

# Backends application compute typically talks to.
BACKENDS: frozenset[Kind] = frozenset({
    Kind.SQL_DATABASE, Kind.NOSQL_TABLE, Kind.CACHE, Kind.OBJECT_STORAGE,
    Kind.QUEUE, Kind.TOPIC, Kind.FILE_STORAGE, Kind.DATA_WAREHOUSE, Kind.SECRET_STORE,
})

# Compute in the order a load balancer should prefer: a scaling group beats a
# bare instance, and a container platform beats both.
COMPUTE_ORDER: tuple[Kind, ...] = (
    Kind.AUTOSCALING_GROUP, Kind.CONTAINER_SERVICE, Kind.KUBERNETES_CLUSTER,
    Kind.VM, Kind.FUNCTION,
)

# Compute that can be a load balancer target.
LB_TARGETS: tuple[Kind, ...] = (
    Kind.AUTOSCALING_GROUP, Kind.CONTAINER_SERVICE, Kind.VM, Kind.KUBERNETES_CLUSTER,
)


class ResourceMapper:
    """Completes a draft spec into a deployable resource graph."""

    def map(self, spec: InfrastructureSpec) -> InfrastructureSpec:
        if not spec.resources:
            return spec

        self._network(spec)
        self._security_groups(spec)
        self._load_balancing(spec)
        self._edge_services(spec)
        self._application_edges(spec)
        self._identity(spec)
        self._observability(spec)
        self._containment_edges(spec)
        return spec

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _add(
        spec: InfrastructureSpec,
        resource_id: str,
        kind: Kind,
        *,
        name: str | None = None,
        count: int = 1,
        origin: Origin = Origin.IMPLIED,
        **properties,
    ) -> Resource:
        info = service_for(kind, spec.provider)
        existing = spec.get(slugify(resource_id))
        if existing is not None:
            existing.properties.update(properties)
            return existing
        resource = Resource(
            id=resource_id,
            kind=kind,
            name=name or info.display,
            tier=info.tier,
            origin=origin,
            count=count,
            properties=properties,
            confidence=1.0,
        )
        spec.resources.append(resource)
        return resource

    # -- stage 1: network --------------------------------------------------

    def _network(self, spec: InfrastructureSpec) -> None:
        needs_vpc = any(r.kind in VPC_SCOPED for r in spec.resources)
        if not needs_vpc:
            if spec.has(Kind.VPC):
                spec.note("A VPC was requested but nothing in the design runs inside it.")
            return

        azs = spec.availability_zones
        vpc = spec.first(Kind.VPC) or self._add(spec, "main", Kind.VPC, name="VPC")
        vpc.properties.setdefault("cidr_block", "10.0.0.0/16")
        vpc.properties.setdefault("enable_dns_hostnames", True)
        if vpc.origin is Origin.IMPLIED:
            spec.note(f"Added a VPC ({vpc.properties['cidr_block']}) to host the workload.")

        public = spec.first(Kind.SUBNET_PUBLIC) or self._add(
            spec, "public", Kind.SUBNET_PUBLIC, name="Public Subnets"
        )
        public.count = azs
        public.properties.update(
            {"cidr_prefix": "10.0.{}.0/24", "offset": 1, "map_public_ip": True}
        )

        # Private subnets are only worth creating when something can live in them.
        private_workloads = [
            r for r in spec.resources
            if r.kind in (VPC_SCOPED - {Kind.LOAD_BALANCER, Kind.BASTION})
        ]
        private = spec.first(Kind.SUBNET_PRIVATE)
        if private_workloads or private:
            private = private or self._add(
                spec, "private", Kind.SUBNET_PRIVATE, name="Private Subnets"
            )
            private.count = azs
            private.properties.update(
                {"cidr_prefix": "10.0.{}.0/24", "offset": 11, "map_public_ip": False}
            )
            spec.connect(vpc.id, private.id, EdgeKind.CONTAINMENT)

        spec.connect(vpc.id, public.id, EdgeKind.CONTAINMENT)
        spec.note(f"Subnets are spread across {azs} availability zones.")

        igw = spec.first(Kind.INTERNET_GATEWAY) or self._add(
            spec, "igw", Kind.INTERNET_GATEWAY
        )
        spec.connect(igw.id, vpc.id, EdgeKind.CONTAINMENT, "attached to")

        self._add(spec, "public_rt", Kind.ROUTE_TABLE, name="Public Route Table",
                  scope="public")

        if private is not None:
            nat = spec.first(Kind.NAT_GATEWAY)
            if nat is None:
                nat = self._add(spec, "nat", Kind.NAT_GATEWAY)
                spec.note(
                    "Added a NAT gateway so private workloads have outbound internet access."
                )
            nat.count = azs if spec.high_availability else 1
            if nat.count > 1:
                spec.note(f"One NAT gateway per AZ ({nat.count} total) for high availability.")
            self._add(spec, "private_rt", Kind.ROUTE_TABLE, name="Private Route Table",
                      scope="private")
            spec.connect(nat.id, igw.id, EdgeKind.TRAFFIC, "egress")

    # -- stage 2: security groups -----------------------------------------

    def _security_groups(self, spec: InfrastructureSpec) -> None:
        if not spec.has(Kind.VPC):
            return

        lb = spec.first(Kind.LOAD_BALANCER)
        app = self._primary_compute(spec)
        db = spec.first(Kind.SQL_DATABASE)
        cache = spec.first(Kind.CACHE)
        bastion = spec.first(Kind.BASTION)

        # A security group the *user* asked for becomes the app SG. Groups this
        # stage created on earlier runs must not be picked up here.
        generic = next(
            (sg for sg in spec.of_kind(Kind.SECURITY_GROUP) if sg.origin is Origin.EXPLICIT),
            None,
        )

        if lb is not None:
            sg = self._add(
                spec, "alb_sg", Kind.SECURITY_GROUP, name="ALB Security Group",
                ingress_from="0.0.0.0/0",
                ingress_ports=[80, 443],
                purpose="load-balancer",
            )
            spec.connect(sg.id, lb.id, EdgeKind.DEPENDENCY, "protects")

        if app is not None:
            sg_id = generic.id if generic is not None else "app_sg"
            sg = self._add(
                spec, sg_id, Kind.SECURITY_GROUP, name="App Security Group",
                ingress_from="alb_sg" if lb is not None else "0.0.0.0/0",
                ingress_ports=[80, 8080] if lb is not None else [80, 443],
                purpose="application",
            )
            if lb is None and generic is None:
                spec.warn(
                    "Application compute is reachable from 0.0.0.0/0 because no load "
                    "balancer was requested. Restrict the ingress CIDR before deploying."
                )
            spec.connect(sg.id, app.id, EdgeKind.DEPENDENCY, "protects")

        if db is not None:
            port = 3306 if str(db.properties.get("engine", "")).startswith("mysql") else 5432
            db.properties.setdefault("port", port)
            sg = self._add(
                spec, "db_sg", Kind.SECURITY_GROUP, name="Database Security Group",
                ingress_from="app_sg" if app is not None else "vpc",
                ingress_ports=[port],
                purpose="database",
            )
            spec.connect(sg.id, db.id, EdgeKind.DEPENDENCY, "protects")

        if cache is not None:
            sg = self._add(
                spec, "cache_sg", Kind.SECURITY_GROUP, name="Cache Security Group",
                ingress_from="app_sg" if app is not None else "vpc",
                ingress_ports=[6379],
                purpose="cache",
            )
            spec.connect(sg.id, cache.id, EdgeKind.DEPENDENCY, "protects")

        if bastion is not None:
            sg = self._add(
                spec, "bastion_sg", Kind.SECURITY_GROUP, name="Bastion Security Group",
                ingress_from="0.0.0.0/0", ingress_ports=[22], purpose="bastion",
            )
            spec.connect(sg.id, bastion.id, EdgeKind.DEPENDENCY, "protects")
            spec.warn(
                "The bastion host accepts SSH from 0.0.0.0/0. Narrow this to your "
                "office or VPN range before deploying."
            )
            if app is not None:
                spec.connect(bastion.id, app.id, EdgeKind.DEPENDENCY, "admin access")

    # -- stage 3: load balancing ------------------------------------------

    def _load_balancing(self, spec: InfrastructureSpec) -> None:
        lb = spec.first(Kind.LOAD_BALANCER)
        if lb is None:
            return
        target = self._primary_compute(spec, kinds=LB_TARGETS)
        if target is None:
            spec.warn("A load balancer was requested but there is no compute to route to.")
            return

        tg = self._add(
            spec, "app_tg", Kind.TARGET_GROUP, name="Target Group",
            port=lb.properties.get("listener_port", 80),
            protocol="HTTP",
            health_check_path="/health",
        )
        spec.connect(lb.id, tg.id, EdgeKind.TRAFFIC, "forwards")
        spec.connect(tg.id, target.id, EdgeKind.TRAFFIC, "targets")
        lb.properties.setdefault("scheme", "internet-facing")

    # -- stage 4: edge services -------------------------------------------

    def _edge_services(self, spec: InfrastructureSpec) -> None:
        cdn = spec.first(Kind.CDN)
        lb = spec.first(Kind.LOAD_BALANCER)
        api = spec.first(Kind.API_GATEWAY)
        bucket = spec.first(Kind.OBJECT_STORAGE)
        dns = spec.first(Kind.DNS_ZONE)
        waf = spec.first(Kind.WAF)

        if cdn is not None:
            if bucket is not None:
                cdn.properties.setdefault("origin", bucket.id)
                cdn.properties.setdefault("origin_type", "s3")
                spec.connect(cdn.id, bucket.id, EdgeKind.TRAFFIC, "origin")
                bucket.properties["cdn_fronted"] = True
                if bucket.properties.get("public_read"):
                    # CloudFront reaches the bucket through origin access
                    # control, so the bucket itself stays private.
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
                spec.warn("CloudFront was requested but no origin (S3 bucket or ALB) exists.")

        if api is not None:
            fn = spec.first(Kind.FUNCTION)
            if fn is not None:
                spec.connect(api.id, fn.id, EdgeKind.TRAFFIC, "invokes")
                api.properties.setdefault("integration", "lambda")
            elif lb is not None:
                spec.connect(api.id, lb.id, EdgeKind.TRAFFIC, "proxies")
                api.properties.setdefault("integration", "http_proxy")
            else:
                spec.warn("API Gateway was requested but has no backend integration.")

        if waf is not None:
            attach = cdn or api or lb
            if attach is not None:
                waf.properties.setdefault("scope", "CLOUDFRONT" if attach is cdn else "REGIONAL")
                spec.connect(waf.id, attach.id, EdgeKind.DEPENDENCY, "inspects")

        if dns is not None:
            entry = cdn or api or lb
            if entry is not None:
                dns.properties.setdefault("alias_target", entry.id)
                spec.connect(dns.id, entry.id, EdgeKind.TRAFFIC, "alias")

    # -- stage 5: application data flow -----------------------------------

    def _application_edges(self, spec: InfrastructureSpec) -> None:
        compute = [r for r in spec.resources if r.kind in COMPUTE]
        backends = [r for r in spec.resources if r.kind in BACKENDS]
        for c in compute:
            for b in backends:
                label = {
                    Kind.SQL_DATABASE: "queries",
                    Kind.NOSQL_TABLE: "reads/writes",
                    Kind.CACHE: "caches",
                    Kind.OBJECT_STORAGE: "objects",
                    Kind.QUEUE: "enqueues",
                    Kind.TOPIC: "publishes",
                    Kind.FILE_STORAGE: "mounts",
                    Kind.SECRET_STORE: "reads",
                    Kind.DATA_WAREHOUSE: "loads",
                }.get(b.kind, "uses")
                spec.connect(c.id, b.id, EdgeKind.DATA, label)

        queue = spec.first(Kind.QUEUE)
        fn = spec.first(Kind.FUNCTION)
        if queue is not None and fn is not None:
            spec.connect(queue.id, fn.id, EdgeKind.TRAFFIC, "triggers")

        registry = spec.first(Kind.CONTAINER_REGISTRY)
        for runner in spec.of_kind(Kind.CONTAINER_SERVICE, Kind.KUBERNETES_CLUSTER):
            if registry is not None:
                spec.connect(registry.id, runner.id, EdgeKind.DEPENDENCY, "images")

    # -- stage 6: identity -------------------------------------------------

    def _identity(self, spec: InfrastructureSpec) -> None:
        compute = [r for r in spec.resources if r.kind in COMPUTE]
        if not compute:
            return
        for c in compute:
            if c.kind is Kind.FUNCTION:
                role = self._add(
                    spec, "lambda_role", Kind.IAM_ROLE, name="Lambda Execution Role",
                    service="lambda.amazonaws.com",
                )
            elif c.kind in (Kind.VM, Kind.AUTOSCALING_GROUP, Kind.BASTION):
                role = self._add(
                    spec, "instance_role", Kind.IAM_ROLE, name="EC2 Instance Role",
                    service="ec2.amazonaws.com",
                )
            elif c.kind is Kind.CONTAINER_SERVICE:
                role = self._add(
                    spec, "task_role", Kind.IAM_ROLE, name="ECS Task Execution Role",
                    service="ecs-tasks.amazonaws.com",
                )
            else:
                role = self._add(
                    spec, "cluster_role", Kind.IAM_ROLE, name="EKS Cluster Role",
                    service="eks.amazonaws.com",
                )
            spec.connect(role.id, c.id, EdgeKind.DEPENDENCY, "assumed by")

        if spec.has(Kind.SQL_DATABASE) and not spec.has(Kind.SECRET_STORE):
            secret = self._add(
                spec, "db_secret", Kind.SECRET_STORE, name="DB Credentials",
                description="Master credentials for the managed database",
            )
            db = spec.first(Kind.SQL_DATABASE)
            if db is not None:
                spec.connect(secret.id, db.id, EdgeKind.DEPENDENCY, "credentials")
            # This secret is created after _application_edges has run, so wire
            # the readers up here rather than leaving it until a second pass.
            for c in compute:
                spec.connect(c.id, secret.id, EdgeKind.DATA, "reads")
            spec.note("Database credentials are stored in Secrets Manager, not in the code.")

    # -- stage 7: observability -------------------------------------------

    def _observability(self, spec: InfrastructureSpec) -> None:
        if spec.environment != "prod" or spec.has(Kind.MONITORING):
            return
        monitored = [r for r in spec.resources if r.kind in COMPUTE or r.kind is Kind.SQL_DATABASE]
        if not monitored:
            return
        cw = self._add(spec, "monitoring", Kind.MONITORING, name="CloudWatch Alarms")
        for r in monitored:
            spec.connect(cw.id, r.id, EdgeKind.DEPENDENCY, "watches")
        spec.note("Production environment: CloudWatch alarms added for compute and database.")

    # -- stage 8: containment ---------------------------------------------

    def _containment_edges(self, spec: InfrastructureSpec) -> None:
        """Record which subnet band each VPC-scoped resource belongs to."""
        public_band = {Kind.LOAD_BALANCER, Kind.BASTION, Kind.NAT_GATEWAY}
        for r in spec.resources:
            if r.kind in VPC_SCOPED or r.kind is Kind.NAT_GATEWAY:
                r.properties.setdefault(
                    "subnet_band", "public" if r.kind in public_band else "private"
                )

    # -- selection ---------------------------------------------------------

    @staticmethod
    def _primary_compute(
        spec: InfrastructureSpec, kinds: tuple[Kind, ...] = COMPUTE_ORDER
    ) -> Resource | None:
        """Pick the compute resource that fronts the application.

        Preference order matters: an ASG beats a bare instance, and a
        container platform beats both, because that is what a load balancer
        should be pointed at when several are present.
        """
        for kind in COMPUTE_ORDER:
            if kind not in kinds:
                continue
            found = spec.first(kind)
            if found is not None:
                return found
        return None
