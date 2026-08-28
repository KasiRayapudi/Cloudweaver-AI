"""Every supported resource must reach the Terraform output, and nothing else may.

Two directions, both of which have caught real defects:

* **Forward** -- a resource in the shared model that emits no HCL is a thing
  the diagram shows and the code never creates.
* **Reverse** -- HCL for something absent from the model is a resource the user
  would deploy without ever seeing it. An SNS alerts topic used to appear this
  way whenever monitoring was requested.
"""

from __future__ import annotations

import re

import pytest

from app.engine.mapper import ResourceMapper
from app.generators.terraform.generator import TerraformGenerator
from app.models.ir import InfrastructureSpec, Kind, Resource
from app.nlp.catalog import service_for
from app.nlp.rule_extractor import RuleExtractor

RESOURCE_TYPE_RE = re.compile(r'^resource\s+"([\w-]+)"', re.MULTILINE)

#: Alarms need something to alarm on. A lone monitoring resource emitting an
#: alert topic and no alarms is the phantom this suite exists to prevent, so
#: monitoring is covered by its own paired test below instead.
NEEDS_A_SUBJECT: frozenset[Kind] = frozenset({Kind.MONITORING})

#: Terraform resources with no counterpart in the model because they are
#: implementation details of one that does: sub-resources, attachments and
#: generated values. Anything not on this list must map to a model resource.
IMPLEMENTATION_DETAIL: frozenset[str] = frozenset({
    "random_password", "random_id",
    "aws_route_table_association", "aws_iam_instance_profile",
    "aws_iam_role_policy_attachment", "aws_iam_role_policy",
    "aws_lb_listener", "aws_db_subnet_group", "aws_elasticache_subnet_group",
    "aws_redshift_subnet_group", "aws_cloudwatch_log_group",
    "aws_s3_bucket_versioning", "aws_s3_bucket_public_access_block",
    "aws_s3_bucket_server_side_encryption_configuration",
    "aws_cloudfront_origin_access_control", "aws_apigatewayv2_stage",
    "aws_apigatewayv2_integration", "aws_apigatewayv2_route",
    "aws_lambda_permission", "aws_lambda_event_source_mapping",
    "aws_secretsmanager_secret_version", "aws_kms_alias",
    "aws_ecs_cluster", "aws_ecs_task_definition", "aws_eks_node_group",
    "aws_launch_template", "aws_autoscaling_policy", "aws_efs_mount_target",
    "aws_route53_record", "aws_sns_topic",
})


def probe(kind: Kind) -> tuple[InfrastructureSpec, dict[str, str]]:
    """A minimal design containing exactly one resource of ``kind``."""
    info = service_for(kind)
    spec = InfrastructureSpec(name="probe", environment="dev")
    spec.add(Resource(id=f"probe_{kind.value}", kind=kind, name=info.display,
                      tier=info.tier, reason="probe"))
    ResourceMapper().map(spec)
    return spec, TerraformGenerator().generate(spec)


def hcl(files: dict[str, str]) -> str:
    return "\n".join(v for k, v in files.items() if k.endswith(".tf"))


@pytest.mark.parametrize(
    "kind", [k for k in Kind if k not in NEEDS_A_SUBJECT], ids=lambda k: k.value
)
def test_every_kind_emits_its_terraform_resource(kind):
    _, files = probe(kind)
    expected = service_for(kind).terraform_type
    assert f'"{expected}"' in hcl(files), (
        f"{kind.value} is in the catalog as {expected} but emits no Terraform"
    )


def test_monitoring_emits_alarms_when_there_is_something_to_watch():
    spec = ResourceMapper().map(
        RuleExtractor().extract("an EC2 instance with cloudwatch monitoring")
    )
    text = hcl(TerraformGenerator().generate(spec))
    assert 'resource "aws_cloudwatch_metric_alarm"' in text


def test_monitoring_alone_emits_nothing():
    """No alarms means no alert topic either -- the topic would be a phantom."""
    _, files = probe(Kind.MONITORING)
    assert 'aws_sns_topic' not in hcl(files)
    assert "monitoring.tf" not in files


@pytest.mark.parametrize(
    "kind", [k for k in Kind if k not in NEEDS_A_SUBJECT], ids=lambda k: k.value
)
def test_no_terraform_resource_is_invented(kind):
    """Every emitted resource maps to the model or is a declared detail."""
    spec, files = probe(kind)
    modelled = {service_for(r.kind).terraform_type for r in spec.resources}
    for emitted in set(RESOURCE_TYPE_RE.findall(hcl(files))):
        assert emitted in modelled or emitted in IMPLEMENTATION_DETAIL, (
            f"{kind.value} produced {emitted}, which is in neither the shared "
            "model nor the implementation-detail allowlist"
        )


@pytest.mark.parametrize("kind", list(Kind), ids=lambda k: k.value)
def test_probe_designs_are_structurally_valid(kind):
    spec, files = probe(kind)
    text = hcl(files)
    assert text.count("{") == text.count("}")
    assert spec.find_cycles() == []
