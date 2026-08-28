"""AWS naming and shape constraints.

`terraform validate` checks syntax and provider *schema*. It does not check
provider *semantics*: an over-long load balancer name and an Aurora engine on
`aws_db_instance` both validate cleanly and are then rejected by the AWS API at
apply time. Everything in this module covers that gap -- rules the API enforces
that Terraform cannot see.

Two uses:

* :func:`fit` shortens a name deterministically so the generated code stays
  inside the limit.
* :func:`check` reports what would still fail, for the validation engine.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.models.ir import InfrastructureSpec, Kind


@dataclass(frozen=True)
class NameRule:
    """One AWS resource naming limit."""

    label: str
    max_length: int
    #: Characters allowed beyond alphanumerics.
    pattern: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9-]+$")
    may_end_with_hyphen: bool = False


# Limits AWS enforces at the API. Sources are the service quotas pages; the
# ones here are the resources this generator emits with a caller-chosen name.
NAME_RULES: dict[str, NameRule] = {
    "load_balancer": NameRule("Load balancer name", 32),
    "target_group": NameRule("Target group name", 32),
    "db_instance": NameRule("RDS identifier", 63),
    "db_cluster": NameRule("Aurora cluster identifier", 63),
    "cache_cluster": NameRule("ElastiCache replication group id", 40),
    "redshift_cluster": NameRule("Redshift cluster identifier", 63),
    "s3_bucket": NameRule("S3 bucket name", 63, re.compile(r"^[a-z0-9.\-]+$")),
    "iam_role": NameRule("IAM role name", 64, re.compile(r"^[\w+=,.@-]+$")),
    "security_group": NameRule("Security group name", 255),
    "ecs_cluster": NameRule("ECS cluster name", 255),
    "eks_cluster": NameRule("EKS cluster name", 100),
    "sqs_queue": NameRule("SQS queue name", 80),
    "sns_topic": NameRule("SNS topic name", 256),
    "lambda_function": NameRule("Lambda function name", 64),
}

#: Longest suffix the generator appends to ``name_prefix`` for a length-capped
#: resource, e.g. "-alb", "-tg", "-redis". Budgeting for it keeps the final
#: name inside the limit without the caller having to think about it.
MAX_SUFFIX = len("-redis")

#: The tightest limit any emitted resource has (ALB and target group, 32).
TIGHTEST_LIMIT = 32


def fit(name: str, limit: int) -> str:
    """Shorten ``name`` to ``limit`` characters, stably and readably.

    A plain truncation collides: two projects sharing a prefix would produce
    the same load balancer name. Instead the tail is replaced with a short
    digest of the full name, so the result stays unique, deterministic across
    runs, and still recognisable.
    """
    if len(name) <= limit:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]
    keep = max(1, limit - len(digest) - 1)
    return f"{name[:keep].rstrip('-')}-{digest}"


def prefix_budget(limit: int = TIGHTEST_LIMIT) -> int:
    """How long ``name_prefix`` may be for every emitted name to fit."""
    return max(8, limit - MAX_SUFFIX)


def check(spec: InfrastructureSpec) -> list[tuple[str, str, str]]:
    """Return ``(severity, code, message)`` for constraints the design breaks.

    Kept independent of the validator's Finding type so this module has no
    dependency on the validation engine, and can be reused by the CLI.
    """
    problems: list[tuple[str, str, str]] = []
    prefix = f"{spec.name}-{spec.environment}"

    budget = prefix_budget()
    if len(prefix) > budget:
        problems.append((
            "info", "name_prefix_shortened",
            f"The name prefix {prefix!r} is {len(prefix)} characters; names for "
            f"load balancers and target groups are capped at {TIGHTEST_LIMIT}, "
            "so they are shortened with a stable digest.",
        ))

    if not re.match(r"^[a-z0-9-]+$", spec.name):
        problems.append((
            "warning", "invalid_project_name",
            f"The project name {spec.name!r} contains characters AWS rejects in "
            "resource names; only lowercase letters, digits and hyphens are safe.",
        ))

    # An ALB must have subnets in at least two availability zones.
    if spec.has(Kind.LOAD_BALANCER) and spec.availability_zones < 2:
        problems.append((
            "error", "load_balancer_single_az",
            f"A load balancer requires subnets in at least two availability "
            f"zones, but only {spec.availability_zones} was requested. The "
            "deployment will fail at apply.",
        ))

    # Multi-AZ RDS needs two AZs for the same reason.
    for db in spec.of_kind(Kind.SQL_DATABASE):
        if db.properties.get("multi_az") and spec.availability_zones < 2:
            problems.append((
                "error", "multi_az_single_zone",
                "A Multi-AZ database needs at least two availability zones.",
            ))

    # Aurora must not be emitted as a single instance.
    for db in spec.of_kind(Kind.SQL_DATABASE):
        engine = str(db.properties.get("engine", ""))
        if engine.startswith("aurora"):
            problems.append((
                "error", "aurora_as_instance",
                f"{db.name} uses the Aurora engine {engine!r} on a standalone "
                "instance. Aurora must be an aws_rds_cluster; this passes "
                "terraform validate and fails at apply.",
            ))

    # EKS spans subnets in two AZs.
    if spec.has(Kind.KUBERNETES_CLUSTER) and spec.availability_zones < 2:
        problems.append((
            "error", "eks_single_az",
            "An EKS cluster requires subnets in at least two availability zones.",
        ))

    return problems
