"""Dependency policy: the single rule about what may be generated.

    A resource may exist only if the user asked for it, or if a resource the
    user asked for cannot be deployed without it.

There is no third category.  "Best practice", "production ready" and
"recommended" are not reasons to create infrastructure someone did not ask
for -- they are reasons to raise a *finding*, which the validator does.

This module is the whole ruleset, as data.  The mapper is a fixed-point loop
over these tables and contains no per-service knowledge of its own, so adding
a service is an edit here rather than a new branch in the engine.

Two tables:

``REQUIREMENTS``
    Mandatory dependencies. Deployment fails without them.

``NON_DEPENDENCIES``
    Services that a human architect might reasonably pair with a given
    resource, recorded explicitly so the intent is documented and testable.
    Nothing in this table is ever generated automatically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.models.ir import InfrastructureSpec, Kind

Condition = Callable[[InfrastructureSpec], bool]


@dataclass(frozen=True)
class Requirement:
    """One mandatory dependency, with the justification shown to the user."""

    kind: Kind
    reason: str
    #: Only applies when this returns True. ``None`` means "always".
    when: Condition | None = None
    #: Extra properties to seed onto the created resource.
    properties: dict = field(default_factory=dict)
    #: Fixed resource id. When set, "does this already exist?" is answered by
    #: id rather than by kind, which is how one design gets several security
    #: groups or IAM roles -- one per consumer -- without ever getting two of
    #: the same one. Without it, a database and a load balancer would share a
    #: single security group, and an ECS task would inherit the EC2 role.
    id_hint: str | None = None

    def applies(self, spec: InfrastructureSpec) -> bool:
        return self.when is None or self.when(spec)

    def satisfied_by(self, spec: InfrastructureSpec) -> bool:
        if self.id_hint is not None:
            return spec.get(self.id_hint) is not None
        return spec.has(self.kind)


# --------------------------------------------------------------------------
# conditions
# --------------------------------------------------------------------------

def wants_private_placement(spec: InfrastructureSpec) -> bool:
    """True only when the user asked for private networking.

    Deliberately not "a private subnet exists": a database creates private
    subnets for itself, and if that were enough to count as intent it would
    pull the web server in with it and make a NAT gateway look mandatory.
    This is the rule that stopped the generator inventing private subnets and
    a NAT gateway for a single public EC2 instance.
    """
    return spec.private_placement_requested


def wants_public_placement(spec: InfrastructureSpec) -> bool:
    return not wants_private_placement(spec)


def has_private_compute(spec: InfrastructureSpec) -> bool:
    """True when something in a private subnet needs outbound internet.

    A managed database does not: RDS pulls no packages and needs no egress, so
    a private database alone never justifies a NAT gateway. Only compute that
    the user placed privately does.

    This runs during dependency closure, before subnet placement has been
    recorded on each resource, so it reads the stated intent rather than the
    ``subnet_band`` property -- which is not populated yet.
    """
    if not spec.has(Kind.SUBNET_PRIVATE) or not spec.private_placement_requested:
        return False
    return any(r.kind in PRIVATE_EGRESS_CONSUMERS for r in spec.resources)


PRIVATE_EGRESS_CONSUMERS: frozenset[Kind] = frozenset({
    Kind.VM, Kind.AUTOSCALING_GROUP, Kind.CONTAINER_SERVICE,
    Kind.KUBERNETES_CLUSTER, Kind.BASTION,
})

#: Anything CloudFront can point at.
CDN_ORIGINS: frozenset[Kind] = frozenset({
    Kind.OBJECT_STORAGE, Kind.LOAD_BALANCER, Kind.API_GATEWAY,
})


def cdn_has_no_origin(spec: InfrastructureSpec) -> bool:
    """True when a CloudFront distribution would have nothing to serve.

    ``aws_cloudfront_distribution`` requires an ``origin`` block with a
    ``domain_name``; without one the generated Terraform does not plan. An
    origin is therefore a mandatory dependency rather than a recommendation,
    and a bucket is the only origin that can be created from nothing.
    """
    return spec.has(Kind.CDN) and not any(spec.has(k) for k in CDN_ORIGINS)


# --------------------------------------------------------------------------
# mandatory dependencies
# --------------------------------------------------------------------------

# Compute that runs customer code and therefore needs a network home, a
# firewall and an identity.
_VPC_COMPUTE = (
    Requirement(Kind.VPC, "{name} must launch inside a VPC."),
    Requirement(
        Kind.SUBNET_PUBLIC,
        "{name} needs a subnet to launch into; public was used because no "
        "private subnet was requested.",
        when=wants_public_placement,
    ),
    Requirement(
        Kind.SUBNET_PRIVATE,
        "{name} launches into the private subnet that was requested.",
        when=wants_private_placement,
    ),
    Requirement(Kind.SECURITY_GROUP, "{name} requires a security group.",
                id_hint="app_sg", properties={"purpose": "application"}),
)

REQUIREMENTS: dict[Kind, tuple[Requirement, ...]] = {
    # -- compute ---------------------------------------------------------
    Kind.VM: _VPC_COMPUTE + (
        Requirement(Kind.IAM_ROLE, "EC2 instances need an instance profile role.",
                    id_hint="instance_role",
                    properties={"service": "ec2.amazonaws.com"}),
    ),
    Kind.AUTOSCALING_GROUP: _VPC_COMPUTE + (
        Requirement(Kind.IAM_ROLE, "Scaled instances need an instance profile role.",
                    id_hint="instance_role",
                    properties={"service": "ec2.amazonaws.com"}),
    ),
    Kind.BASTION: (
        Requirement(Kind.VPC, "A bastion host must launch inside a VPC."),
        Requirement(Kind.SUBNET_PUBLIC, "A bastion host must be reachable from outside."),
        Requirement(Kind.SECURITY_GROUP, "A bastion host requires a security group.",
                    id_hint="bastion_sg", properties={"purpose": "bastion"}),
        Requirement(Kind.IAM_ROLE, "The bastion host needs an instance profile role.",
                    id_hint="instance_role",
                    properties={"service": "ec2.amazonaws.com"}),
    ),
    Kind.CONTAINER_SERVICE: _VPC_COMPUTE + (
        Requirement(Kind.IAM_ROLE, "ECS tasks need an execution role to pull images and log.",
                    id_hint="task_role",
                    properties={"service": "ecs-tasks.amazonaws.com"}),
    ),
    Kind.KUBERNETES_CLUSTER: (
        Requirement(Kind.VPC, "An EKS cluster must be created inside a VPC."),
        Requirement(
            Kind.SUBNET_PUBLIC,
            "EKS requires subnets in at least two availability zones.",
            when=wants_public_placement,
        ),
        Requirement(
            Kind.SUBNET_PRIVATE,
            "EKS worker nodes launch into the private subnets requested.",
            when=wants_private_placement,
        ),
        Requirement(Kind.SECURITY_GROUP, "The cluster requires a security group.",
                    id_hint="app_sg", properties={"purpose": "application"}),
        Requirement(Kind.IAM_ROLE, "EKS requires a cluster service role.",
                    id_hint="cluster_role", properties={"service": "eks.amazonaws.com"}),
    ),
    Kind.FUNCTION: (
        Requirement(Kind.IAM_ROLE, "Lambda requires an execution role.",
                    id_hint="lambda_role",
                    properties={"service": "lambda.amazonaws.com"}),
    ),

    # -- networking ------------------------------------------------------
    # A VPC is the root of the network graph and depends on nothing.
    Kind.VPC: (),
    Kind.SUBNET_PUBLIC: (
        Requirement(Kind.VPC, "A subnet must belong to a VPC."),
        Requirement(Kind.INTERNET_GATEWAY,
                    "A public subnet is only public with an internet gateway."),
        Requirement(Kind.ROUTE_TABLE,
                    "The public subnet needs a route table with a default route.",
                    id_hint="public_rt", properties={"scope": "public"}),
    ),
    Kind.SUBNET_PRIVATE: (
        Requirement(Kind.VPC, "A subnet must belong to a VPC."),
        Requirement(Kind.ROUTE_TABLE,
                    "The private subnet needs its own route table.",
                    id_hint="private_rt", properties={"scope": "private"}),
        Requirement(
            Kind.NAT_GATEWAY,
            "Compute in the private subnet needs outbound internet access.",
            when=has_private_compute,
        ),
    ),
    Kind.INTERNET_GATEWAY: (
        Requirement(Kind.VPC, "An internet gateway attaches to a VPC."),
    ),
    Kind.NAT_GATEWAY: (
        Requirement(Kind.VPC, "A NAT gateway lives in a VPC."),
        Requirement(Kind.SUBNET_PUBLIC, "A NAT gateway must sit in a public subnet."),
        Requirement(Kind.INTERNET_GATEWAY,
                    "A NAT gateway routes outbound traffic through the internet gateway."),
        Requirement(Kind.ELASTIC_IP, "A NAT gateway requires an Elastic IP."),
    ),
    Kind.ROUTE_TABLE: (
        Requirement(Kind.VPC, "A route table belongs to a VPC."),
    ),
    Kind.SECURITY_GROUP: (
        Requirement(Kind.VPC, "A security group belongs to a VPC."),
    ),
    Kind.ELASTIC_IP: (),

    # -- traffic ---------------------------------------------------------
    Kind.LOAD_BALANCER: (
        Requirement(Kind.VPC, "A load balancer must live in a VPC."),
        Requirement(Kind.SUBNET_PUBLIC,
                    "An internet-facing load balancer needs public subnets."),
        Requirement(Kind.SECURITY_GROUP, "The load balancer requires a security group.",
                    id_hint="alb_sg", properties={"purpose": "load-balancer"}),
        Requirement(Kind.TARGET_GROUP, "A load balancer listener must forward to a target group."),
    ),
    Kind.TARGET_GROUP: (
        Requirement(Kind.VPC, "A target group is scoped to a VPC."),
    ),
    Kind.API_GATEWAY: (),
    Kind.CDN: (
        Requirement(
            Kind.OBJECT_STORAGE,
            "A CloudFront distribution cannot be created without an origin, "
            "so a bucket was added for it to serve.",
            when=cdn_has_no_origin,
        ),
    ),
    Kind.DNS_ZONE: (),
    Kind.WAF: (),

    # -- data ------------------------------------------------------------
    Kind.SQL_DATABASE: (
        Requirement(Kind.VPC, "A managed database must live in a VPC."),
        Requirement(
            Kind.SUBNET_PRIVATE,
            "Databases are placed in private subnets so they are not "
            "reachable from the internet.",
        ),
        Requirement(Kind.SECURITY_GROUP, "The database requires a security group.",
                    id_hint="db_sg", properties={"purpose": "database"}),
    ),
    Kind.CACHE: (
        Requirement(Kind.VPC, "An ElastiCache cluster must live in a VPC."),
        Requirement(Kind.SUBNET_PRIVATE, "Caches are placed in private subnets."),
        Requirement(Kind.SECURITY_GROUP, "The cache requires a security group.",
                    id_hint="cache_sg", properties={"purpose": "cache"}),
    ),
    Kind.DATA_WAREHOUSE: (
        Requirement(Kind.VPC, "A Redshift cluster must live in a VPC."),
        Requirement(Kind.SUBNET_PRIVATE, "The warehouse is placed in private subnets."),
        Requirement(Kind.SECURITY_GROUP, "The warehouse requires a security group.",
                    id_hint="warehouse_sg", properties={"purpose": "database"}),
    ),
    Kind.FILE_STORAGE: (
        Requirement(Kind.VPC, "EFS mount targets live inside a VPC."),
        Requirement(Kind.SECURITY_GROUP, "Mount targets require a security group.",
                    id_hint="app_sg", properties={"purpose": "application"}),
    ),
    Kind.NOSQL_TABLE: (),
    Kind.OBJECT_STORAGE: (),

    # -- integration / security / ops ------------------------------------
    Kind.QUEUE: (),
    Kind.TOPIC: (),
    Kind.EVENT_BUS: (),
    Kind.IAM_ROLE: (),
    Kind.SECRET_STORE: (),
    Kind.KEY_MANAGEMENT: (),
    Kind.MONITORING: (),
    Kind.CONTAINER_REGISTRY: (),
}


# --------------------------------------------------------------------------
# deliberately NOT dependencies
# --------------------------------------------------------------------------

#: Things a production architecture would plausibly add, listed so that the
#: decision not to generate them is explicit, reviewable and testable rather
#: than an accident of control flow. The validator may *suggest* these; the
#: mapper never creates them.
NON_DEPENDENCIES: dict[Kind, tuple[tuple[Kind, str], ...]] = {
    Kind.VM: (
        (Kind.LOAD_BALANCER, "A single instance does not need a load balancer."),
        (Kind.AUTOSCALING_GROUP, "Scaling was not requested."),
        (Kind.MONITORING, "Alarms are a recommendation, not a deployment requirement."),
        (Kind.ELASTIC_IP, "An instance in a public subnet already gets a public IP."),
    ),
    Kind.SQL_DATABASE: (
        (Kind.SECRET_STORE, "Terraform generates the password; a secret store is optional."),
        (Kind.MONITORING, "Database alarms are a recommendation."),
        (Kind.CACHE, "A cache is an optimisation, not a dependency."),
    ),
    Kind.FUNCTION: (
        (Kind.API_GATEWAY, "A function can be invoked by many things besides HTTP."),
        (Kind.QUEUE, "Asynchronous invocation was not requested."),
        (Kind.VPC, "Lambda runs outside a VPC unless it needs VPC resources."),
    ),
    Kind.OBJECT_STORAGE: (
        (Kind.CDN, "A bucket does not require CloudFront."),
    ),
    Kind.KUBERNETES_CLUSTER: (
        (Kind.LOAD_BALANCER,
         "EKS provisions load balancers through Kubernetes Service objects at "
         "runtime, so Terraform does not need to create one."),
        (Kind.CONTAINER_REGISTRY, "Images may come from any registry."),
    ),
    Kind.CONTAINER_SERVICE: (
        (Kind.LOAD_BALANCER, "A service can run without being internet facing."),
        (Kind.CONTAINER_REGISTRY, "Images may come from any registry."),
    ),
}


def requirements_for(kind: Kind) -> tuple[Requirement, ...]:
    return REQUIREMENTS.get(kind, ())


def is_never_auto_generated(parent: Kind, candidate: Kind) -> bool:
    """True if ``candidate`` is explicitly recorded as not implied by ``parent``."""
    return any(k is candidate for k, _ in NON_DEPENDENCIES.get(parent, ()))
