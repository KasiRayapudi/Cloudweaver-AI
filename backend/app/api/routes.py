"""HTTP routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.api.schemas import (
    ExampleModel,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
)
from app.config import get_settings
from app.engine.pipeline import Pipeline
from app.export.bundle import build_zip

logger = logging.getLogger(__name__)
router = APIRouter()

VERSION = "1.0.0"

_pipeline: Pipeline | None = None


def get_pipeline() -> Pipeline:
    """Lazily built so importing the module never touches the environment."""
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline


EXAMPLES: list[ExampleModel] = [
    ExampleModel(
        title="Three-tier web application",
        prompt=(
            "I need a production three-tier web app on AWS in eu-west-1: an "
            "auto scaling group of EC2 instances behind an application load "
            "balancer, a Multi-AZ PostgreSQL database, a Redis cache, and an S3 "
            "bucket for user uploads. It has to be highly available."
        ),
    ),
    ExampleModel(
        title="Serverless REST API",
        prompt=(
            "A serverless REST API: API Gateway in front of a Python Lambda "
            "function that reads and writes a DynamoDB table, with an SQS queue "
            "for background jobs. Development environment in ap-south-1."
        ),
    ),
    ExampleModel(
        title="Static site on a CDN",
        prompt=(
            "Host a static website in an S3 bucket served through CloudFront "
            "with a Route 53 custom domain and a WAF in front of it."
        ),
    ),
    ExampleModel(
        title="Containerised microservice",
        prompt=(
            "Production ECS Fargate service behind a load balancer, pulling "
            "images from ECR, using an Aurora MySQL database and a NAT gateway "
            "for outbound traffic. Highly available across 3 availability zones."
        ),
    ),
    ExampleModel(
        title="Kubernetes platform",
        prompt=(
            "Set up an EKS cluster with 4 t3.medium nodes in private subnets, "
            "a bastion host for admin access, an S3 bucket for artifacts and "
            "CloudWatch monitoring. Production in us-west-2."
        ),
    ),
]


@router.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=VERSION,
        extractor=settings.extractor,
        llm_available=get_pipeline().llm_extractor.available,
    )


@router.get("/examples", response_model=list[ExampleModel], tags=["meta"])
def examples() -> list[ExampleModel]:
    return EXAMPLES


@router.post("/generate", response_model=GenerateResponse, tags=["generation"])
def generate(request: GenerateRequest) -> GenerateResponse:
    """Run the full pipeline: NL -> IR -> diagram + Terraform."""
    try:
        result = get_pipeline().run(request.prompt, extractor=request.extractor)
    except Exception as exc:  # pragma: no cover - unexpected generator failure
        logger.exception("Generation failed")
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}") from exc

    if not result.spec.resources:
        raise HTTPException(
            status_code=422,
            detail=(
                "No cloud resources could be identified in that description. "
                "Name the services you need, for example: 'two EC2 web servers "
                "behind a load balancer with a PostgreSQL database'."
            ),
        )
    return GenerateResponse(**result.to_dict())


@router.post("/generate/download", tags=["generation"])
def download(request: GenerateRequest) -> Response:
    """Same pipeline, returned as a zipped Terraform project."""
    result = get_pipeline().run(request.prompt, extractor=request.extractor)
    if not result.spec.resources:
        raise HTTPException(status_code=422, detail="Nothing to package.")
    payload = build_zip(result)
    filename = f"{result.spec.name or 'infrastructure'}.zip"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
