"""Packaging: turn a generation result into a downloadable project."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.engine.pipeline import GenerationResult


def _manifest(result: GenerationResult) -> str:
    return json.dumps(
        {
            "generator": "ai-infra-iac-generator",
            "prompt": result.spec.prompt,
            "summary": result.spec.summary,
            "spec": result.spec.model_dump(mode="json"),
            "findings": [f.to_dict() for f in result.findings],
            "estimated_monthly_cost_usd": result.estimated_monthly_cost,
        },
        indent=2,
    )


def build_zip(result: GenerationResult) -> bytes:
    """Return a zip archive containing Terraform, both diagrams and a manifest."""
    buffer = io.BytesIO()
    root = result.spec.name or "infrastructure"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, content in result.terraform.items():
            archive.writestr(f"{root}/terraform/{filename}", content)
        archive.writestr(f"{root}/diagram/architecture.svg", result.diagram_svg)
        archive.writestr(f"{root}/diagram/architecture.mmd", result.diagram_mermaid)
        archive.writestr(f"{root}/spec.json", _manifest(result))
    return buffer.getvalue()


def write_project(result: GenerationResult, destination: Path) -> list[Path]:
    """Write the same layout to disk. Used by the CLI."""
    written: list[Path] = []
    terraform_dir = destination / "terraform"
    diagram_dir = destination / "diagram"
    terraform_dir.mkdir(parents=True, exist_ok=True)
    diagram_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in result.terraform.items():
        path = terraform_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)

    svg_path = diagram_dir / "architecture.svg"
    svg_path.write_text(result.diagram_svg, encoding="utf-8")
    written.append(svg_path)

    mmd_path = diagram_dir / "architecture.mmd"
    mmd_path.write_text(result.diagram_mermaid, encoding="utf-8")
    written.append(mmd_path)

    spec_path = destination / "spec.json"
    spec_path.write_text(_manifest(result), encoding="utf-8")
    written.append(spec_path)
    return written
