"""Run `terraform validate` against generated projects.

    python scripts/tf_validate.py            # the representative corpus
    python scripts/tf_validate.py "prompt"   # one ad-hoc prompt

`terraform validate` checks syntax and provider *schema*: whether an argument
exists and has the right type. It does not check provider *semantics* -- an
`aws_db_instance` with an Aurora engine validates cleanly and then fails at
apply. That gap is why `engine/constraints.py` exists; the two checks are
complementary rather than redundant.

Exits non-zero if any project fails, so it can gate CI.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine.pipeline import Pipeline  # noqa: E402

# One prompt per structurally distinct shape the generator can produce.
CORPUS: dict[str, str] = {
    "single-ec2": (
        "Create a development environment in us-east-1 with one Ubuntu EC2 "
        "instance inside a VPC. Add an Internet Gateway, Security Group "
        "allowing SSH and HTTP, IAM role, and Elastic IP."
    ),
    "three-tier-ha": (
        "A production three-tier web app in eu-west-1: an auto scaling group of "
        "EC2 instances in private subnets behind an application load balancer, "
        "a Multi-AZ PostgreSQL database, a Redis cache and an S3 bucket. "
        "Highly available."
    ),
    "serverless": (
        "API Gateway in front of a Python Lambda function that reads and writes "
        "a DynamoDB table, with an SQS queue for background jobs."
    ),
    "static-site": (
        "Host a static website in an S3 bucket served through CloudFront with a "
        "Route 53 custom domain and a WAF."
    ),
    "containers-ecs": (
        "A production ECS Fargate service behind a load balancer, pulling images "
        "from ECR, using a MySQL database in private subnets."
    ),
    "kubernetes": (
        "An EKS cluster with 4 t3.medium worker nodes, an ingress load balancer, "
        "a bastion host and an S3 bucket for artifacts."
    ),
    "aurora": "An Aurora PostgreSQL cluster with a web server in private subnets.",
    "networking-only": (
        "A VPC with public and private subnets, an internet gateway, a NAT "
        "gateway and route tables in ap-south-1."
    ),
    "storage-only": "An S3 bucket, an EFS file system and a KMS key.",
    "monitoring-only": "An EC2 instance with CloudWatch monitoring and an SNS topic.",
    "data-warehouse": "A Redshift data warehouse in private subnets with an S3 bucket.",
    "windows": "A Windows Server 2022 EC2 instance with RDP access and an Elastic IP.",
    "https-alb": (
        "An application load balancer with HTTPS and an ACM certificate in "
        "front of an auto scaling group of web servers."
    ),
    "network-lb": (
        "A network load balancer with TLS termination on port 443 in front of "
        "two web servers."
    ),
    "gateway-lb": "A gateway load balancer for a firewall appliance fleet in private subnets.",
}

BOOTSTRAP_VERSIONS = """terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.40" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }
}
"""


def find_terraform() -> str | None:
    found = shutil.which("terraform")
    if found:
        return found
    # winget installs outside PATH until the shell restarts.
    packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        for candidate in packages.glob("Hashicorp.Terraform*/terraform.exe"):
            return str(candidate)
    return None


def _env() -> dict[str, str]:
    environment = dict(os.environ)
    environment["TF_IN_AUTOMATION"] = "1"
    return environment


def bootstrap(terraform: str, workdir: Path) -> tuple[bool, str]:
    """Install providers once into a shared working directory.

    Terraform copies providers into every working directory it initialises --
    roughly 600 MB apiece on Windows, where the plugin cache cannot symlink.
    Initialising one directory per project filled a 200 GB disk within twelve
    projects. Instead a single directory is initialised with the union of
    providers the generator can emit, and each project's files are swapped into
    it, so the provider is downloaded exactly once.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "versions.tf").write_text(BOOTSTRAP_VERSIONS, encoding="utf-8")
    result = subprocess.run(
        [terraform, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=workdir, capture_output=True, text=True, env=_env(),
    )
    return result.returncode == 0, result.stdout + result.stderr


def validate_in_place(
    terraform: str, workdir: Path, files: dict[str, str]
) -> tuple[bool, str]:
    """Replace the configuration in the shared directory and validate it."""
    for stale in workdir.iterdir():
        if stale.name in (".terraform", ".terraform.lock.hcl"):
            continue  # the whole point: keep the installed providers
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
        else:
            stale.unlink()

    # Everything the project ships, including user_data.sh and lambda sources:
    # `file()` and `archive_file` are resolved during validate, so a missing
    # support file fails the check for a reason that has nothing to do with the
    # configuration being correct.
    for filename, content in files.items():
        if filename in (".gitignore", "README.md"):
            continue
        path = workdir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    result = subprocess.run(
        [terraform, "validate", "-no-color"],
        cwd=workdir, capture_output=True, text=True, env=_env(),
    )
    return result.returncode == 0, result.stdout + result.stderr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", help="Validate one ad-hoc prompt.")
    parser.add_argument("--keep", action="store_true", help="Keep the working directory.")
    args = parser.parse_args(argv)

    terraform = find_terraform()
    if terraform is None:
        print("terraform not found on PATH; install it to run this check.", file=sys.stderr)
        return 2

    cases = {"ad-hoc": args.prompt} if args.prompt else CORPUS
    workdir = Path(tempfile.mkdtemp(prefix="tfvalidate-"))
    pipeline = Pipeline()
    failures: list[str] = []

    print(f"terraform: {terraform}")
    print(f"workdir:   {workdir}\n")

    ok, output = bootstrap(terraform, workdir)
    if not ok:
        print("terraform init failed:\n" + output, file=sys.stderr)
        shutil.rmtree(workdir, ignore_errors=True)
        return 2

    for name, prompt in cases.items():
        result = pipeline.run(prompt)
        if not result.terraform:
            print(f"  {name:18} SKIP  (no resources extracted)")
            continue
        ok, output = validate_in_place(terraform, workdir, result.terraform)
        count = len(result.spec.resources)
        if ok:
            print(f"  {name:18} PASS  ({count} resources)")
        else:
            failures.append(name)
            print(f"  {name:18} FAIL  ({count} resources)")
            for line in output.strip().splitlines()[:14]:
                print(f"      {line}")

    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n{len(cases) - len(failures)}/{len(cases)} projects valid")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
