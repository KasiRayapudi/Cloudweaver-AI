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


class ExtractionModel(BaseModel):
    """Why one resource is in the design -- the confidence record."""

    resource: str
    id: str
    kind: str
    origin: Literal["explicit", "implied"]
    confidence: float
    reason: str
    source: str | None = None


class ExclusionModel(BaseModel):
    """A service the requirement explicitly ruled out."""

    kind: str
    phrase: str
    cue: str
    reason: str
    evidence: str


class RecommendationModel(BaseModel):
    """One improvement the optimiser suggests. Advice, never a resource."""

    id: str
    category: str
    priority: str
    title: str
    reason: str
    action: str
    resources: list[str]
    difficulty: str
    monthly_delta_usd: float
    confidence: float
    pillar: str


class OptimizationSummaryModel(BaseModel):
    total: int
    by_category: dict[str, int]
    by_priority: dict[str, int]
    potential_monthly_saving_usd: float
    additional_monthly_spend_usd: float


class ExplanationModel(BaseModel):
    """Everything deterministically known about one resource."""

    resource_id: str
    name: str
    kind: str
    requested: bool
    origin: str
    reason: str
    rule: str | None = None
    triggered_by: str | None = None
    evidence: str | None = None
    confidence: float
    external_id: str | None = None
    depends_on: list[str]
    required_by: list[str]
    monthly_cost_usd: float
    terraform_snippet: str | None = None
    terraform_file: str | None = None
    pillar: str
    security_notes: str | None = None
    networking_notes: str | None = None
    operational_notes: str | None = None
    alternatives: list[dict[str, str]]
    best_practices: list[str]
    finding_codes: list[str]
    recommendation_ids: list[str]


class DependencyGraphModel(BaseModel):
    #: resource id -> ids it must be created after
    edges: dict[str, list[str]]
    creation_order: list[str]
    cycles: list[list[str]]


class GenerateResponse(BaseModel):
    summary: SummaryModel
    spec: dict[str, Any]
    diagram: DiagramModel
    terraform: dict[str, str]
    findings: list[FindingModel]
    recommendations: list[RecommendationModel]
    optimization: OptimizationSummaryModel
    explanations: list[ExplanationModel]
    #: Questions worth asking about this particular design. Computed by the
    #: architect so the panel opens with something useful rather than a
    #: fixed list that may not apply.
    suggestions: list[str]
    dependency_graph: DependencyGraphModel
    extraction: list[ExtractionModel]
    exclusions: list[ExclusionModel]


class AskRequest(BaseModel):
    """A question about a design, plus the prompt that produced it.

    The design is regenerated from the prompt rather than being held in a
    session. Generation is deterministic and takes tens of milliseconds, so
    the same prompt gives the same design every time -- which means the
    assistant needs no server-side state to be correct.
    """

    prompt: str = Field(..., min_length=1, max_length=8000)
    question: str = Field(..., min_length=1, max_length=2000)
    extractor: Literal["rule", "llm"] | None = None


class AnswerModel(BaseModel):
    question: str
    intent: str
    text: str
    sources: list[str]
    deterministic: bool
    confidence: float
    resources: list[str]
    recommendations: list[RecommendationModel]
    findings: list[FindingModel]
    code: str | None = None
    follow_ups: list[str]


class ExportRequest(BaseModel):
    """Ask for one artefact from a design.

    The prompt is sent rather than a session id: generation is deterministic,
    so the export is guaranteed to describe the same design the user is
    looking at, with no server-side state to fall out of sync.
    """

    prompt: str = Field(..., min_length=1, max_length=8000)
    extractor: Literal["rule", "llm"] | None = None
    #: "auto" follows the reader's system theme and is right for the embedded
    #: viewer. Every other value is fixed, which is what an exported figure
    #: needs -- a diagram that inverts on the reviewer's laptop is a defect.
    theme: Literal["auto", "light", "dark", "print"] = "light"
    transparent: bool = False


class ExampleModel(BaseModel):
    title: str
    prompt: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    extractor: str
    llm_available: bool
