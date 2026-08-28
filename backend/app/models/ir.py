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
    """Where a resource came from -- used by the UI and the assumption log."""

    EXPLICIT = "explicit"  # the user named it
    IMPLIED = "implied"    # required by something the user named
    DEFAULT = "default"    # added by a baseline policy (e.g. always add a VPC)


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
