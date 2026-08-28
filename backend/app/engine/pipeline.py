"""The end-to-end pipeline described in section VI of the paper.

    text -> extract -> map -> validate -> {diagram, terraform}

One call, one shared ``InfrastructureSpec``, two outputs.  The result object
carries the spec itself so callers can see exactly what the generators saw.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.engine.emission import audit as audit_emission
from app.engine.mapper import ResourceMapper
from app.engine.validator import Finding, SpecValidator, estimate_monthly_cost
from app.generators.diagram.layout import LayoutEngine
from app.generators.diagram.mermaid import MermaidRenderer
from app.generators.diagram.svg_renderer import SvgRenderer
from app.generators.terraform.generator import TerraformGenerator
from app.models.ir import InfrastructureSpec
from app.nlp.base import ExtractionError, Extractor
from app.nlp.llm_extractor import LLMExtractor
from app.nlp.rule_extractor import RuleExtractor

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    spec: InfrastructureSpec
    diagram_svg: str
    diagram_mermaid: str
    terraform: dict[str, str]
    findings: list[Finding] = field(default_factory=list)
    estimated_monthly_cost: float = 0.0
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "spec": self.spec.model_dump(mode="json"),
            "diagram": {"svg": self.diagram_svg, "mermaid": self.diagram_mermaid},
            "terraform": self.terraform,
            "findings": [f.to_dict() for f in self.findings],
            "dependency_graph": {
                "edges": self.spec.dependency_graph(),
                "creation_order": self.spec.creation_order(),
                "cycles": self.spec.find_cycles(),
            },
            "exclusions": [
                {
                    "kind": e.kind.value,
                    "phrase": e.phrase,
                    "cue": e.cue,
                    "reason": e.reason,
                    "evidence": e.evidence,
                }
                for e in self.spec.exclusions
            ],
            "extraction": [
                {
                    "resource": r.name,
                    "id": r.id,
                    "kind": r.kind.value,
                    "origin": r.origin.value,
                    "confidence": r.confidence,
                    "reason": r.reason,
                    "source": r.evidence,
                }
                for r in self.spec.resources
            ],
            "summary": {
                "name": self.spec.name,
                "provider": self.spec.provider.value,
                "region": self.spec.region,
                "environment": self.spec.environment,
                "description": self.spec.summary,
                "resource_count": len(self.spec.resources),
                "file_count": len(self.terraform),
                "estimated_monthly_cost_usd": self.estimated_monthly_cost,
                "extractor": self.spec.extractor,
                "duration_ms": round(self.duration_ms, 1),
            },
        }


class Pipeline:
    """Wires the four stages together and owns extractor selection."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.rule_extractor = RuleExtractor()
        self.llm_extractor = LLMExtractor(
            api_key=self.settings.anthropic_api_key, model=self.settings.model
        )
        self.mapper = ResourceMapper()
        self.validator = SpecValidator()
        self.terraform = TerraformGenerator()
        self.layout = LayoutEngine()
        self.svg = SvgRenderer()
        self.mermaid = MermaidRenderer()

    # -- extractor selection ----------------------------------------------

    def _choose(self, requested: str | None) -> tuple[Extractor, str | None]:
        """Return the extractor to use plus a warning if we had to fall back."""
        mode = (requested or self.settings.extractor).lower()
        if mode == "llm":
            if self.llm_extractor.available:
                return self.llm_extractor, None
            return self.rule_extractor, (
                "LLM extraction was requested but is not configured "
                "(set ANTHROPIC_API_KEY and install the anthropic package). "
                "Used the offline rule-based extractor instead."
            )
        return self.rule_extractor, None

    # -- the pipeline ------------------------------------------------------

    def run(self, prompt: str, extractor: str | None = None) -> GenerationResult:
        started = time.perf_counter()
        prompt = (prompt or "").strip()[: self.settings.max_prompt_chars]

        chosen, fallback_warning = self._choose(extractor)

        # Stage 1+2: NLP extraction.
        try:
            spec = chosen.extract(prompt)
        except ExtractionError as exc:
            logger.warning("Extractor %s failed: %s", chosen.name, exc)
            spec = self.rule_extractor.extract(prompt)
            spec.warn(f"{exc} Fell back to the offline rule-based extractor.")
        if fallback_warning:
            spec.warn(fallback_warning)

        # Stage 3: resource mapping.
        spec = self.mapper.map(spec)

        # Stage 3b: validation and policy checks.
        findings = self.validator.validate(spec)

        # Stages 4+5: the two outputs, both from `spec`.
        layout = self.layout.build(spec)
        svg = self.svg.render(layout)
        mermaid = self.mermaid.render(spec)
        terraform = self.terraform.generate(spec) if spec.resources else {}

        # Stage 5b: confirm the code contains what the model describes. This
        # runs after generation because it compares the two artefacts, which is
        # the only way to catch a resource the generator silently dropped.
        findings += [
            Finding(severity, code, message)  # type: ignore[arg-type]
            for severity, code, message in audit_emission(spec, terraform)
        ]

        return GenerationResult(
            spec=spec,
            diagram_svg=svg,
            diagram_mermaid=mermaid,
            terraform=terraform,
            findings=findings,
            estimated_monthly_cost=estimate_monthly_cost(spec),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
