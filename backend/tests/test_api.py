"""Tests for the pipeline, validator, packaging and HTTP API."""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.engine.validator import SpecValidator, estimate_monthly_cost
from app.export.bundle import build_zip, write_project
from app.main import app
from app.models.ir import Kind
from tests.conftest import SERVERLESS, THREE_TIER


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# -- pipeline --------------------------------------------------------------

def test_pipeline_produces_all_outputs(three_tier):
    assert three_tier.spec.resources
    assert three_tier.diagram_svg.startswith("<svg")
    assert three_tier.diagram_mermaid.startswith("flowchart")
    assert three_tier.terraform
    assert three_tier.duration_ms > 0


def test_pipeline_falls_back_when_llm_unavailable(pipeline):
    result = pipeline.run(THREE_TIER, extractor="llm")
    assert result.spec.extractor == "rule"
    assert any("LLM" in w for w in result.spec.warnings)


def test_pipeline_survives_nonsense_input(pipeline):
    result = pipeline.run("banana banana banana")
    assert result.spec.resources == []
    assert result.terraform == {}
    assert result.diagram_svg  # still renders an empty canvas


def test_pipeline_truncates_oversized_prompts(pipeline):
    result = pipeline.run("a web server " + "x" * 10000)
    assert len(result.spec.prompt) <= pipeline.settings.max_prompt_chars


# -- validator -------------------------------------------------------------

def test_open_ssh_is_an_error(pipeline):
    result = pipeline.run("a bastion host and a web server")
    codes = {f.code for f in result.findings}
    assert "open_admin_port" in codes
    assert any(f.severity == "error" for f in result.findings)


def test_production_single_az_is_flagged(pipeline):
    result = pipeline.run("a production web server with a postgres database")
    assert "prod_single_az" in {f.code for f in result.findings}


def test_highly_available_production_is_not_flagged(three_tier):
    assert "prod_single_az" not in {f.code for f in three_tier.findings}


def test_clean_serverless_design_has_no_errors(serverless):
    assert not [f for f in serverless.findings if f.severity == "error"]


def test_no_dangling_edges_are_reported(three_tier):
    assert "dangling_edge" not in {f.code for f in three_tier.findings}


def test_cost_estimate_grows_with_the_design(pipeline):
    small = pipeline.run("a dynamodb table")
    large = pipeline.run(THREE_TIER)
    assert estimate_monthly_cost(large.spec) > estimate_monthly_cost(small.spec)


def test_validator_reports_findings_as_dicts(three_tier):
    for finding in SpecValidator().validate(three_tier.spec):
        payload = finding.to_dict()
        assert payload["severity"] in ("error", "warning", "info")
        assert payload["message"]


# -- packaging -------------------------------------------------------------

def test_zip_contains_terraform_and_diagrams(three_tier):
    archive = zipfile.ZipFile(io.BytesIO(build_zip(three_tier)))
    names = archive.namelist()
    assert any(n.endswith("terraform/versions.tf") for n in names)
    assert any(n.endswith("diagram/architecture.svg") for n in names)
    assert any(n.endswith("diagram/architecture.mmd") for n in names)
    assert any(n.endswith("spec.json") for n in names)
    assert archive.testzip() is None


def test_write_project_creates_files_on_disk(three_tier, tmp_path):
    written = write_project(three_tier, tmp_path)
    assert written
    assert (tmp_path / "terraform" / "versions.tf").is_file()
    assert (tmp_path / "diagram" / "architecture.svg").is_file()
    assert (tmp_path / "spec.json").is_file()


# -- HTTP API --------------------------------------------------------------

def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["extractor"] in ("rule", "llm")


def test_examples_are_offered(client):
    body = client.get("/api/examples").json()
    assert len(body) >= 3
    assert all(e["prompt"] for e in body)


def test_generate_returns_everything_the_ui_needs(client):
    response = client.post("/api/generate", json={"prompt": SERVERLESS})
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["resource_count"] > 0
    assert body["diagram"]["svg"].startswith("<svg")
    assert "versions.tf" in body["terraform"]
    assert isinstance(body["findings"], list)
    assert body["spec"]["resources"]


def test_generate_rejects_unusable_input(client):
    response = client.post("/api/generate", json={"prompt": "hello how are you"})
    assert response.status_code == 422


def test_generate_validates_prompt_length(client):
    assert client.post("/api/generate", json={"prompt": "hi"}).status_code == 422


def test_download_returns_a_zip(client):
    response = client.post("/api/generate/download", json={"prompt": THREE_TIER})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None


def test_spec_round_trips_through_json(three_tier):
    from app.models.ir import InfrastructureSpec

    restored = InfrastructureSpec.model_validate(three_tier.spec.model_dump(mode="json"))
    assert restored.resource_count == three_tier.spec.resource_count
    assert restored.first(Kind.SQL_DATABASE) is not None
