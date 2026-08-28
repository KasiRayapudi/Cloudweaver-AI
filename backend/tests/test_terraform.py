"""Tests for the Terraform generator.

The `terraform` binary is not assumed to be present, so validity is checked
structurally instead: balanced delimiters, and -- the useful one -- every
``aws_x.y`` reference resolving to a resource the project actually declares.
That catches the failure mode a code generator really has, which is emitting a
reference to something it forgot to create.
"""

from __future__ import annotations

import re

import pytest

from app.generators.terraform.generator import TerraformGenerator
from app.generators.terraform.hcl import Block, HclFile, Raw, ref, render_value
from app.models.ir import Kind
from tests.conftest import ALL_PROMPTS

DECLARATION_RE = re.compile(r'^(resource|data)\s+"([\w-]+)"\s+"([\w-]+)"', re.MULTILINE)
REFERENCE_RE = re.compile(r"(?<![\w.\"])((?:data\.)?(?:aws|random|archive)_[\w]+\.[\w]+)")
VARIABLE_DECL_RE = re.compile(r'^variable\s+"([\w-]+)"', re.MULTILINE)
VARIABLE_REF_RE = re.compile(r"\bvar\.([\w]+)")


def project(prompt: str, pipeline) -> dict[str, str]:
    return pipeline.run(prompt).terraform


def hcl_text(files: dict[str, str]) -> str:
    return "\n".join(v for k, v in files.items() if k.endswith(".tf"))


# -- the HCL writer itself -------------------------------------------------

def test_render_primitives():
    assert render_value("a") == '"a"'
    assert render_value(True) == "true"
    assert render_value(7) == "7"
    assert render_value(None) == "null"
    assert render_value(Raw("aws_vpc.main.id")) == "aws_vpc.main.id"
    assert render_value(["a", "b"]) == '["a", "b"]'


def test_render_escapes_quotes():
    assert render_value('say "hi"') == '"say \\"hi\\""'


def test_block_aligns_attributes():
    block = Block("resource", "aws_vpc", "main")
    block.set("cidr_block", "10.0.0.0/16").set("enable_dns_hostnames", True)
    rendered = block.render()
    assert 'cidr_block           = "10.0.0.0/16"' in rendered
    assert "enable_dns_hostnames = true" in rendered


def test_nested_block_has_no_leading_blank_line():
    block = Block("resource", "aws_lb", "main")
    block.block("access_logs").set("enabled", True)
    lines = block.render().splitlines()
    assert lines[1].strip() == "access_logs {"


def test_ref_helper():
    assert ref("aws_vpc", "main", "id") == Raw("aws_vpc.main.id")


def test_unsupported_type_is_rejected():
    with pytest.raises(TypeError):
        render_value(object())


def test_empty_file_is_falsey():
    assert not HclFile()


# -- generated projects ----------------------------------------------------

@pytest.mark.parametrize("prompt", ALL_PROMPTS)
def test_braces_are_balanced(prompt, pipeline):
    text = hcl_text(project(prompt, pipeline))
    assert text.count("{") == text.count("}")
    assert text.count("[") == text.count("]")


@pytest.mark.parametrize("prompt", ALL_PROMPTS)
def test_every_reference_resolves(prompt, pipeline):
    """No generated file may reference a resource the project never declares."""
    files = project(prompt, pipeline)
    text = hcl_text(files)

    declared = set()
    for block_type, tf_type, name in DECLARATION_RE.findall(text):
        prefix = "data." if block_type == "data" else ""
        declared.add(f"{prefix}{tf_type}.{name}")

    referenced = set(REFERENCE_RE.findall(text))
    # Strip the attribute suffix: aws_vpc.main.id -> aws_vpc.main
    unresolved = {r for r in referenced if r not in declared}
    assert not unresolved, f"dangling references: {sorted(unresolved)}"


@pytest.mark.parametrize("prompt", ALL_PROMPTS)
def test_every_variable_is_declared(prompt, pipeline):
    files = project(prompt, pipeline)
    text = hcl_text(files)
    declared = set(VARIABLE_DECL_RE.findall(text))
    used = set(VARIABLE_REF_RE.findall(text))
    assert not (used - declared), f"undeclared variables: {sorted(used - declared)}"


@pytest.mark.parametrize("prompt", ALL_PROMPTS)
def test_core_files_always_present(prompt, pipeline):
    files = project(prompt, pipeline)
    for expected in ("versions.tf", "variables.tf", "locals.tf", "outputs.tf",
                     "terraform.tfvars", "README.md", ".gitignore"):
        assert expected in files


def test_three_tier_emits_expected_resources(three_tier):
    text = hcl_text(three_tier.terraform)
    for expected in (
        'resource "aws_vpc"',
        'resource "aws_subnet" "public"',
        'resource "aws_subnet" "private"',
        'resource "aws_lb"',
        'resource "aws_lb_target_group"',
        'resource "aws_lb_listener"',
        'resource "aws_autoscaling_group"',
        'resource "aws_launch_template"',
        'resource "aws_db_instance"',
        'resource "aws_elasticache_replication_group"',
        'resource "aws_s3_bucket"',
        'resource "aws_iam_role"',
    ):
        assert expected in text, expected


def test_serverless_project_has_no_vpc(serverless):
    text = hcl_text(serverless.terraform)
    assert 'resource "aws_vpc"' not in text
    assert 'resource "aws_lambda_function"' in text
    assert 'resource "aws_dynamodb_table"' in text
    assert 'resource "aws_apigatewayv2_api"' in text


def test_database_password_is_never_hardcoded(three_tier):
    text = hcl_text(three_tier.terraform)
    assert "random_password.db.result" in text
    assert re.search(r'password\s*=\s*"', text) is None


def test_production_database_is_protected(three_tier):
    text = hcl_text(three_tier.terraform)
    assert "deletion_protection          = true" in text
    assert "publicly_accessible          = false" in text


def test_storage_is_encrypted(three_tier):
    text = hcl_text(three_tier.terraform)
    assert "storage_encrypted            = true" in text
    assert "aws_s3_bucket_server_side_encryption_configuration" in text


def test_lambda_source_is_packaged_not_referenced_blindly(serverless):
    files = serverless.terraform
    assert 'data "archive_file"' in files["compute.tf"]
    assert "lambda/index.py" in files


def test_user_data_script_is_emitted_when_instances_exist(three_tier):
    assert "user_data.sh" in three_tier.terraform
    assert "#!/bin/bash" in three_tier.terraform["user_data.sh"]


def test_generation_is_deterministic(pipeline):
    prompt = ALL_PROMPTS[0]
    assert pipeline.run(prompt).terraform == pipeline.run(prompt).terraform


def test_no_resources_means_no_files(pipeline):
    result = pipeline.run("write me a poem about the sea")
    assert result.terraform == {}


def test_generator_handles_every_kind():
    """Every catalog kind must survive generation without raising."""
    from app.engine.mapper import ResourceMapper
    from app.models.ir import InfrastructureSpec, Resource
    from app.nlp.catalog import service_for

    spec = InfrastructureSpec(name="kitchen-sink", environment="prod")
    for kind in Kind:
        info = service_for(kind)
        spec.add(Resource(id=f"r_{kind.value}", kind=kind, name=info.display,
                          tier=info.tier))
    ResourceMapper().map(spec)
    files = TerraformGenerator().generate(spec)
    assert files
    text = hcl_text(files)
    assert text.count("{") == text.count("}")
