"""Command line interface.

    python backend/cli.py "two web servers behind a load balancer" -o ./out

Useful for CI: generate the Terraform for a described environment, run
`terraform plan` against it, and fail the build on any `error` finding.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.engine.pipeline import Pipeline  # noqa: E402
from app.export.bundle import write_project  # noqa: E402

SEVERITY_ICON = {"error": "[!]", "warning": "[~]", "info": "[i]"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iacgen",
        description="Generate a cloud architecture diagram and Terraform from "
                    "a plain-language requirement.",
    )
    parser.add_argument("prompt", nargs="?", help="The requirement. Reads stdin if omitted.")
    parser.add_argument("-o", "--out", type=Path, default=Path("./generated"),
                        help="Output directory (default: ./generated)")
    parser.add_argument("--extractor", choices=("rule", "llm"), default=None,
                        help="NLP backend to use (default: value of EXTRACTOR env var)")
    parser.add_argument("--json", action="store_true",
                        help="Print the full result as JSON instead of a summary")
    parser.add_argument("--print-diagram", action="store_true",
                        help="Print the Mermaid diagram to stdout")
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero if any error-severity finding is raised")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prompt = args.prompt or sys.stdin.read()
    if not prompt.strip():
        print("error: no requirement given", file=sys.stderr)
        return 2

    result = Pipeline().run(prompt, extractor=args.extractor)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    if not result.spec.resources:
        print("No cloud resources were identified in that description.", file=sys.stderr)
        for warning in result.spec.warnings:
            print(f"  {warning}", file=sys.stderr)
        return 1

    written = write_project(result, args.out)
    spec = result.spec

    print(f"\n{spec.name}  ({spec.provider.value} / {spec.region} / {spec.environment})")
    print(f"  {spec.summary}\n")

    print(f"Resources ({len(spec.resources)}):")
    for r in spec.resources:
        label = r.name + (f" x{r.count}" if r.count > 1 else "")
        marker = " " if r.origin.value == "explicit" else "+"
        print(f"  {marker} {label:<30} [{r.id}]")

    if spec.exclusions:
        print("\nExplicitly excluded:")
        for exclusion in spec.exclusions:
            print(f"  - {exclusion.kind.value} ({exclusion.cue!r} in the requirement)")

    if spec.assumptions:
        print("\nAssumptions:")
        for note in spec.assumptions:
            print(f"  - {note}")

    if result.findings:
        print("\nFindings:")
        for finding in result.findings:
            icon = SEVERITY_ICON.get(finding.severity, "[?]")
            print(f"  {icon} {finding.message}")

    if args.print_diagram:
        print("\n" + result.diagram_mermaid)

    print(f"\nWrote {len(written)} files to {args.out.resolve()}")
    print(f"Rough monthly cost: ~${result.estimated_monthly_cost:.0f}")
    print(f"Generated in {result.duration_ms:.0f} ms via the '{spec.extractor}' extractor")

    if args.strict and any(f.severity == "error" for f in result.findings):
        print("\nstrict mode: error-severity findings present", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
