"""Emission audit: does the Terraform contain everything the model describes?

The generator reaches for ``spec.first(kind)`` in many places, which quietly
assumes one resource per kind. When a design legitimately holds two databases
the diagram draws both and the Terraform creates one -- the precise
diagram/code divergence this project exists to prevent, and invisible because
nothing compared the two artefacts.

This module performs that comparison after generation. It cannot make the
generator emit more; it makes a shortfall impossible to ship unnoticed, which
is the difference between a known limitation and a silent data loss.
"""

from __future__ import annotations

import re
from collections import Counter

from app.models.ir import InfrastructureSpec, Kind
from app.nlp.catalog import service_for

DECLARATION_RE = re.compile(r'^resource\s+"([\w-]+)"\s+"([\w-]+)"', re.MULTILINE)

#: Kinds whose emission is conditional by design, so a zero count is correct
#: rather than a defect. Monitoring emits alarms only when something exists to
#: alarm on; the validator reports that case separately.
CONDITIONAL: frozenset[Kind] = frozenset({Kind.MONITORING})


def declared_types(terraform: dict[str, str]) -> Counter[str]:
    """Count ``resource "type" "name"`` declarations across the project."""
    counts: Counter[str] = Counter()
    for filename, content in terraform.items():
        if not filename.endswith(".tf"):
            continue
        for tf_type, _name in DECLARATION_RE.findall(content):
            counts[tf_type] += 1
    return counts


def audit(
    spec: InfrastructureSpec, terraform: dict[str, str]
) -> list[tuple[str, str, str]]:
    """Return ``(severity, code, message)`` for anything the model lost.

    Declarations are compared, not instances: a resource carrying ``count`` is
    one declaration however many machines it creates, which is exactly how the
    model represents it too.
    """
    if not terraform:
        return []

    emitted = declared_types(terraform)

    # Several kinds share a Terraform type -- public and private subnets are
    # both aws_subnet, a bastion is an aws_instance -- so the comparison is by
    # type, not by kind.
    expected: Counter[str] = Counter()
    kinds_for_type: dict[str, set[str]] = {}
    for resource in spec.resources:
        if resource.kind in CONDITIONAL:
            continue
        if resource.is_external:
            # Looked up with a data source, so it correctly produces no
            # `resource` declaration. Counting it as missing would report the
            # feature working as a defect.
            continue
        tf_type = service_for(resource.kind, spec.provider).terraform_type
        expected[tf_type] += 1
        kinds_for_type.setdefault(tf_type, set()).add(resource.name)

    problems: list[tuple[str, str, str]] = []
    for tf_type, wanted in expected.items():
        got = emitted.get(tf_type, 0)
        if got >= wanted:
            continue
        names = ", ".join(sorted(kinds_for_type[tf_type]))
        if got == 0:
            problems.append((
                "error", "resource_not_generated",
                f"{names} is in the design but no {tf_type} was generated, so "
                "the diagram shows infrastructure the code does not create.",
            ))
        else:
            problems.append((
                "error", "duplicate_resource_dropped",
                f"The design holds {wanted} resources of type {tf_type} "
                f"({names}) but only {got} reached the Terraform. Generating "
                "more than one of this service is not yet supported.",
            ))
    return problems
