from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.pipeline import Pipeline  # noqa: E402

THREE_TIER = (
    "I need a production three-tier web app in eu-west-1: an auto scaling group "
    "of EC2 instances behind an application load balancer, a Multi-AZ PostgreSQL "
    "database, a Redis cache and an S3 bucket for uploads. Highly available."
)

SERVERLESS = (
    "A serverless REST API: API Gateway in front of a Python Lambda function "
    "that reads and writes a DynamoDB table, with an SQS queue for background "
    "jobs. Development environment in ap-south-1."
)

STATIC_SITE = (
    "Host a static website in an S3 bucket served through CloudFront with a "
    "Route 53 custom domain and a WAF in front of it."
)

CONTAINERS = (
    "Production ECS Fargate service behind a load balancer, pulling images from "
    "ECR, using an Aurora MySQL database. Highly available across 3 "
    "availability zones."
)

ALL_PROMPTS = [THREE_TIER, SERVERLESS, STATIC_SITE, CONTAINERS]


@pytest.fixture(scope="session")
def pipeline() -> Pipeline:
    return Pipeline()


@pytest.fixture(scope="session")
def three_tier(pipeline: Pipeline):
    return pipeline.run(THREE_TIER)


@pytest.fixture(scope="session")
def serverless(pipeline: Pipeline):
    return pipeline.run(SERVERLESS)
