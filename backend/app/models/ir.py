"""Intermediate Representation (IR) for cloud infrastructure.

This module defines the single shared representation that the whole system is
built around.  Natural language is parsed into an ``InfrastructureSpec``; both
the architecture diagram and the Terraform code are then generated *from that
same object*.  Nothing downstream is allowed to look at the raw user text
again -- that is what keeps the diagram and the code from drifting apart.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class Provider(str, Enum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"


class Tier(str, Enum):
    """Vertical band a resource occupies in the architecture diagram."""

    GLOBAL = "global"      # Route53, IAM, KMS -- outside the VPC
    EDGE = "edge"          # CloudFront, WAF, API Gateway
    PUBLIC = "public"      # ALB, NAT, bastion -- public subnets
    APP = "app"            # EC2, ECS, EKS, Lambda -- private subnets
    DATA = "data"          # RDS, DynamoDB, ElastiCache
    NETWORK = "network"    # VPC, subnets, gateways, route tables
    SECURITY = "security"  # security groups, IAM roles, secrets
    OPS = "ops"            # CloudWatch, SNS alarms


class Kind(str, Enum):
    """Canonical, provider-neutral resource kinds.

    The NLP layer only ever emits values from this enum.  Provider specific
    naming (``aws_instance`` etc.) is decided later, by the catalog.
    """

    # --- networking -----------------------------------------------------
    VPC = "vpc"
    SUBNET_PUBLIC = "subnet_public"
    SUBNET_PRIVATE = "subnet_private"
    INTERNET_GATEWAY = "internet_gateway"
    NAT_GATEWAY = "nat_gateway"
    ROUTE_TABLE = "route_table"
    SECURITY_GROUP = "security_group"
    ELASTIC_IP = "elastic_ip"
    # --- compute --------------------------------------------------------
    VM = "vm"
    AUTOSCALING_GROUP = "autoscaling_group"
    CONTAINER_SERVICE = "container_service"
    KUBERNETES_CLUSTER = "kubernetes_cluster"
    FUNCTION = "function"
    CONTAINER_REGISTRY = "container_registry"
    BASTION = "bastion"
    # --- traffic --------------------------------------------------------
    LOAD_BALANCER = "load_balancer"
    TARGET_GROUP = "target_group"
    API_GATEWAY = "api_gateway"
    CDN = "cdn"
    DNS_ZONE = "dns_zone"
    WAF = "waf"
    # --- data -----------------------------------------------------------
    SQL_DATABASE = "sql_database"
    NOSQL_TABLE = "nosql_table"
    CACHE = "cache"
    OBJECT_STORAGE = "object_storage"
    FILE_STORAGE = "file_storage"
    DATA_WAREHOUSE = "data_warehouse"
    # --- integration ----------------------------------------------------
    QUEUE = "queue"
    TOPIC = "topic"
    EVENT_BUS = "event_bus"
    # --- security / ops -------------------------------------------------
    IAM_ROLE = "iam_role"
    SECRET_STORE = "secret_store"
    KEY_MANAGEMENT = "key_management"
    MONITORING = "monitoring"


class Origin(str, Enum):
    """Why a resource is in the design.

    These two values are exhaustive by policy: a resource is either something
    the user asked for, or something without which the requested resources
    cannot be deployed.  There is deliberately no "best practice" or
    "recommended" origin -- see :mod:`app.engine.policy`.
    """

    EXPLICIT = "explicit"  # the user named it, or named a trigger phrase for it
    REQUIRED = "implied"   # a mandatory dependency of an explicit resource


class EdgeKind(str, Enum):
    TRAFFIC = "traffic"      # request flow
    DATA = "data"            # reads/writes
    CONTAINMENT = "contains"  # VPC contains subnet
    DEPENDENCY = "depends"   # ordering only


_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def slugify(value: str) -> str:
    """Normalise a label into a Terraform-safe identifier."""
    slug = _SLUG_RE.sub("_", value.strip().lower()).strip("_")
    if not slug:
        slug = "resource"
    if slug[0].isdigit():
        slug = f"r_{slug}"
    return slug


class Resource(BaseModel):
    id: str = Field(..., description="Unique, Terraform-safe identifier")
    kind: Kind
    name: str = Field(..., description="Human readable label for the diagram")
    tier: Tier
    origin: Origin = Origin.EXPLICIT
    count: int = Field(1, ge=1, le=100)
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    evidence: str | None = Field(
        None, description="Span of user text that produced this resource"
    )
    reason: str = Field(
        "",
        description=(
            "Why this resource exists: the phrase the user used, or the "
            "dependency that made it mandatory. Never empty in a generated spec."
        ),
    )

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        return slugify(v)


class Edge(BaseModel):
    source: str
    target: str
    kind: EdgeKind = EdgeKind.TRAFFIC
    label: str | None = None

    @model_validator(mode="after")
    def _no_self_loop(self) -> Edge:
        if self.source == self.target:
            raise ValueError(f"self-referential edge on {self.source!r}")
        return self


class InfrastructureSpec(BaseModel):
    """The shared source of truth for the diagram and the IaC output."""

    name: str = "generated-infrastructure"
    provider: Provider = Provider.AWS
    region: str = "us-east-1"
    environment: str = "dev"
    prompt: str = ""
    summary: str = ""
    high_availability: bool = False
    private_placement_requested: bool = Field(
        False,
        description=(
            "True only when the user asked for private networking. A database "
            "forcing private subnets on itself does not set this: it must not "
            "drag public-facing compute into private subnets, which would in "
            "turn make a NAT gateway look mandatory."
        ),
    )
    availability_zones: int = Field(2, ge=1, le=6)
    resources: list[Resource] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    extractor: str = "rule"

    # -- lookup helpers used throughout the generators -------------------

    def get(self, resource_id: str) -> Resource | None:
        for r in self.resources:
            if r.id == resource_id:
                return r
        return None

    def of_kind(self, *kinds: Kind) -> list[Resource]:
        wanted = set(kinds)
        return [r for r in self.resources if r.kind in wanted]

    def first(self, kind: Kind) -> Resource | None:
        found = self.of_kind(kind)
        return found[0] if found else None

    def has(self, kind: Kind) -> bool:
        return bool(self.of_kind(kind))

    def add(self, resource: Resource) -> Resource:
        """Add a resource, de-duplicating on id."""
        existing = self.get(resource.id)
        if existing:
            return existing
        self.resources.append(resource)
        return resource

    def connect(
        self,
        source: str,
        target: str,
        kind: EdgeKind = EdgeKind.TRAFFIC,
        label: str | None = None,
    ) -> None:
        if source == target:
            return
        if not (self.get(source) and self.get(target)):
            return
        for e in self.edges:
            if e.source == source and e.target == target and e.kind == kind:
                return
        self.edges.append(Edge(source=source, target=target, kind=kind, label=label))

    def note(self, message: str) -> None:
        if message not in self.assumptions:
            self.assumptions.append(message)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    @property
    def resource_count(self) -> int:
        return sum(r.count for r in self.resources)

    # -- dependency graph -------------------------------------------------

    def dependency_graph(self) -> dict[str, list[str]]:
        """Map each resource id to the ids it must be created after.

        Derived from ``DEPENDENCY`` and ``CONTAINMENT`` edges only: traffic and
        data edges describe runtime flow, not creation order.
        """
        graph: dict[str, list[str]] = {r.id: [] for r in self.resources}
        for edge in self.edges:
            if edge.kind not in (EdgeKind.DEPENDENCY, EdgeKind.CONTAINMENT):
                continue
            # A depends-on edge points from the provider to the consumer, so
            # the target is what waits for the source.
            graph.setdefault(edge.target, []).append(edge.source)
        return {k: sorted(set(v)) for k, v in graph.items()}

    def creation_order(self) -> list[str]:
        """Topologically sorted resource ids. Cycles are appended at the end."""
        graph = self.dependency_graph()
        ordered: list[str] = []
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(node: str) -> None:
            if node in done or node in visiting:
                return
            visiting.add(node)
            for parent in graph.get(node, []):
                visit(parent)
            visiting.discard(node)
            done.add(node)
            ordered.append(node)

        for resource in self.resources:
            visit(resource.id)
        return ordered

    def find_cycles(self) -> list[list[str]]:
        """Return any dependency cycles, as lists of resource ids."""
        graph = self.dependency_graph()
        cycles: list[list[str]] = []
        state: dict[str, int] = {}  # 0 = unvisited, 1 = on stack, 2 = done
        stack: list[str] = []

        def walk(node: str) -> None:
            state[node] = 1
            stack.append(node)
            for parent in graph.get(node, []):
                if state.get(parent, 0) == 0:
                    walk(parent)
                elif state.get(parent) == 1:
                    cycle = stack[stack.index(parent):]
                    if cycle and sorted(cycle) not in [sorted(c) for c in cycles]:
                        cycles.append(list(cycle))
            stack.pop()
            state[node] = 2

        for resource in self.resources:
            if state.get(resource.id, 0) == 0:
                walk(resource.id)
        return cycles
