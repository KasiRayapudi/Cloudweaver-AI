"""Request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=3,
        max_length=4000,
        description="Plain-language description of the infrastructure required.",
        examples=[
            "Two EC2 web servers behind an application load balancer with a "
            "PostgreSQL database and an S3 bucket for uploads, in eu-west-1."
        ],
    )
    extractor: Literal["rule", "llm"] | None = Field(
        None,
        description="Override the configured NLP backend for this request.",
    )


class FindingModel(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    resource_id: str | None = None


class DiagramModel(BaseModel):
    svg: str
    mermaid: str


class SummaryModel(BaseModel):
    name: str
    provider: str
    region: str
    environment: str
    description: str
    resource_count: int
    file_count: int
    estimated_monthly_cost_usd: float
    extractor: str
    duration_ms: float


class GenerateResponse(BaseModel):
    summary: SummaryModel
    spec: dict[str, Any]
    diagram: DiagramModel
    terraform: dict[str, str]
    findings: list[FindingModel]


class ExampleModel(BaseModel):
    title: str
    prompt: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    extractor: str
    llm_available: bool
