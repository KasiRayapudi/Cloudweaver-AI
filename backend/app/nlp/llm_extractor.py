"""Optional LLM-backed extractor (Anthropic Claude).

The paper's pipeline calls for an AI stage that interprets free-form
requirements.  This module supplies that stage, but the system never *depends*
on it: if the SDK is missing, the key is unset, or the call fails, the caller
falls back to :class:`~app.nlp.rule_extractor.RuleExtractor` and records a
warning on the spec.

The model is constrained to a tool schema built from the ``Kind`` enum, so it
can only emit resources the rest of the pipeline already knows how to draw and
generate code for.  Free-text hallucinations cannot reach the generators.
"""

from __future__ import annotations

import json
import logging

from app.models.ir import InfrastructureSpec, Kind, Origin, Resource, slugify
from app.nlp.base import ExtractionError, Extractor
from app.nlp.catalog import service_for

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are the requirement-extraction stage of an Infrastructure
as Code generator. Read the user's plain-language description of the cloud
infrastructure they want and report the resources it implies.

Rules:
- Only report resources the user actually asked for, explicitly or by clear
  implication of the workload they described.
- Do NOT add supporting plumbing (VPC, subnets, route tables, security groups,
  target groups, IAM roles). A later deterministic stage adds all of that.
- Use `count` for repetition ("three web servers" -> count 3).
- Prefer the most specific kind available. A "Kubernetes cluster" is
  kubernetes_cluster, not vm.
- If the description is not about cloud infrastructure at all, return no
  resources and say so in `summary`.

Call the `report_infrastructure` tool exactly once."""


def _tool_schema() -> dict:
    return {
        "name": "report_infrastructure",
        "description": "Report the cloud resources implied by the user's description.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Short kebab-case name for this infrastructure.",
                },
                "region": {"type": "string", "description": "AWS region id, e.g. us-east-1."},
                "environment": {
                    "type": "string",
                    "enum": ["dev", "test", "qa", "staging", "prod", "sandbox"],
                },
                "high_availability": {"type": "boolean"},
                "availability_zones": {"type": "integer", "minimum": 1, "maximum": 6},
                "summary": {"type": "string", "description": "One sentence describing the design."},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "resources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "snake_case identifier, unique in this list.",
                            },
                            "kind": {"type": "string", "enum": [k.value for k in Kind]},
                            "count": {"type": "integer", "minimum": 1, "maximum": 100},
                            "properties": {
                                "type": "object",
                                "description": (
                                    "Service settings stated by the user, e.g. "
                                    "instance_type, engine, allocated_storage, runtime."
                                ),
                                "additionalProperties": True,
                            },
                            "evidence": {
                                "type": "string",
                                "description": "The phrase from the user text that implied this.",
                            },
                        },
                        "required": ["id", "kind"],
                    },
                },
            },
            "required": ["resources", "region", "environment", "summary"],
        },
    }


class LLMExtractor(Extractor):
    """Claude-backed extraction with a strict tool schema."""

    name = "llm"

    def __init__(self, api_key: str | None = None, model: str = MODEL) -> None:
        self.api_key = api_key
        self.model = model
        self._client = None

    @property
    def available(self) -> bool:
        if not self.api_key:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def extract(self, prompt: str) -> InfrastructureSpec:
        if not self.available:
            raise ExtractionError("LLM extractor is not configured.")

        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=[_tool_schema()],
                tool_choice={"type": "tool", "name": "report_infrastructure"},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # network, auth, rate limit
            raise ExtractionError(f"LLM call failed: {exc}") from exc

        payload = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                payload = block.input
                break
        if payload is None:
            raise ExtractionError("Model did not call the extraction tool.")

        return self._to_spec(prompt, payload)

    # -- payload -> IR -----------------------------------------------------

    def _to_spec(self, prompt: str, payload: dict) -> InfrastructureSpec:
        spec = InfrastructureSpec(
            prompt=prompt,
            extractor=self.name,
            name=slugify(str(payload.get("project_name") or "generated")).replace("_", "-"),
            region=str(payload.get("region") or "us-east-1"),
            environment=str(payload.get("environment") or "dev"),
            high_availability=bool(payload.get("high_availability", False)),
            availability_zones=int(payload.get("availability_zones") or 2),
            summary=str(payload.get("summary") or ""),
        )
        for note in payload.get("assumptions") or []:
            spec.note(str(note))

        for item in payload.get("resources") or []:
            try:
                kind = Kind(item["kind"])
            except (KeyError, ValueError):
                spec.warn(f"Model proposed an unsupported resource kind: {item.get('kind')!r}")
                continue
            info = service_for(kind)
            props = item.get("properties") or {}
            if not isinstance(props, dict):
                props = {}
            spec.add(
                Resource(
                    id=slugify(str(item.get("id") or kind.value)),
                    kind=kind,
                    name=info.display,
                    tier=info.tier,
                    origin=Origin.EXPLICIT,
                    count=max(1, min(100, int(item.get("count") or 1))),
                    properties=props,
                    confidence=0.9,
                    evidence=str(item.get("evidence") or "") or None,
                )
            )

        if not spec.resources:
            spec.warn("The model did not identify any cloud resources in the description.")
        logger.debug("LLM extraction payload: %s", json.dumps(payload)[:2000])
        return spec
