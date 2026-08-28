"""Terraform generator: InfrastructureSpec -> a multi-file HCL project.

The generator reads only the completed IR.  It walks the resource graph once
per output file so the layout of the generated project matches how a human
would organise it (network / security / compute / data / edge / iam), then
returns a mapping of filename -> contents.

Every emitted resource carries the same ``local.tags`` and ``local.name_prefix``
so the deployed infrastructure is traceable back to the prompt that produced it.
"""

from __future__ import annotations

from app.engine.constraints import MAX_SUFFIX, TIGHTEST_LIMIT
from app.generators.terraform.hcl import Block, HclFile, Raw, ref, var
from app.models.ir import InfrastructureSpec, Kind, Resource

TERRAFORM_VERSION = ">= 1.5.0"
AWS_PROVIDER_VERSION = "~> 5.40"


def _name(resource: Resource) -> str:
    """Terraform-safe local name for a resource."""
    return resource.id


def _prefixed(suffix: str) -> Raw:
    return Raw(f'"${{local.name_prefix}}-{suffix}"')


def _capped(suffix: str, limit: int = TIGHTEST_LIMIT) -> Raw:
    """A name AWS will accept even if the project name is long.

    ``local.name_prefix`` is interpolated at apply time, so its final length is
    not known here. Load balancer and target group names are capped at 32
    characters and a descriptive prompt easily exceeds that, producing a plan
    that fails at apply. ``local.name_short`` is the prefix already trimmed to
    fit, computed once in locals.tf.
    """
    return Raw(f'"${{local.name_short}}-{suffix}"')


def _vpc_id(spec: InfrastructureSpec) -> Raw | None:
    """Reference to the VPC, whether this project creates it or reads it.

    Deploying into an existing VPC changes every reference in the project from
    ``aws_vpc.main.id`` to ``data.aws_vpc.main.id``, so the choice lives in one
    function rather than at each of the call sites.
    """
    vpc = spec.first(Kind.VPC)
    if vpc is None:
        return None
    prefix = "data." if vpc.is_external else ""
    return Raw(f"{prefix}aws_vpc.{_name(vpc)}.id")


def _vpc_cidr(spec: InfrastructureSpec) -> Raw:
    """The address range to carve subnets out of.

    For an existing VPC this must be read from the data source: deriving
    subnets from the default var.vpc_cidr would place them outside the range
    the VPC actually owns, and the apply would fail.
    """
    vpc = spec.first(Kind.VPC)
    if vpc is not None and vpc.is_external:
        return Raw(f"data.aws_vpc.{_name(vpc)}.cidr_block")
    return var("vpc_cidr")


def _named_tags(resource: Resource, fallback: str) -> Raw:
    """Name tag using the name the user gave, when they gave one."""
    return _tags(("Name", resource.display_name or fallback))


def _subnet_ids(spec: InfrastructureSpec, subnet: Resource) -> Raw:
    """All ids for a subnet band, whether created here or looked up."""
    if subnet.is_external:
        return Raw(f"data.aws_subnets.{_name(subnet)}.ids")
    return Raw(f"aws_subnet.{_name(subnet)}[*].id")


def _subnet_id(spec: InfrastructureSpec, subnet: Resource, index: str) -> Raw:
    """One subnet id from the band, by index."""
    if subnet.is_external:
        return Raw(f"data.aws_subnets.{_name(subnet)}.ids[{index}]")
    return Raw(f"aws_subnet.{_name(subnet)}[{index}].id")


def _tags(*extra: tuple[str, str]) -> Raw:
    if not extra:
        return Raw("local.tags")
    merged = ", ".join(f'{k} = "{v}"' for k, v in extra)
    return Raw(f"merge(local.tags, {{ {merged} }})")


class TerraformGenerator:
    """Renders an ``InfrastructureSpec`` as a deployable Terraform project."""

    def generate(self, spec: InfrastructureSpec) -> dict[str, str]:
        files: dict[str, str] = {
            "versions.tf": self._versions(spec),
            "variables.tf": self._variables(spec),
            "locals.tf": self._locals(spec),
            "terraform.tfvars": self._tfvars(spec),
        }

        for filename, builder in (
            ("network.tf", self._network),
            ("security.tf", self._security),
            ("compute.tf", self._compute),
            ("data.tf", self._data),
            ("edge.tf", self._edge),
            ("integration.tf", self._integration),
            ("iam.tf", self._iam),
            ("monitoring.tf", self._monitoring),
        ):
            hcl = builder(spec)
            if hcl:
                files[filename] = hcl.render()

        files["outputs.tf"] = self._outputs(spec)
        files.update(self._support_files(spec))
        return files

    # ------------------------------------------------------------------
    # supporting files
    # ------------------------------------------------------------------

    def _support_files(self, spec: InfrastructureSpec) -> dict[str, str]:
        """Non-HCL files the generated project references or needs to run."""
        files: dict[str, str] = {
            ".gitignore": (
                ".terraform/\n"
                "*.tfstate\n"
                "*.tfstate.*\n"
                "crash.log\n"
                "build/\n"
                "*.tfvars.local\n"
                ".terraform.lock.hcl\n"
            )
        }

        if spec.of_kind(Kind.VM, Kind.AUTOSCALING_GROUP):
            files["user_data.sh"] = (
                "#!/bin/bash\n"
                "# Bootstrap script for application instances.\n"
                "set -euo pipefail\n"
                "\n"
                "dnf update -y\n"
                "dnf install -y nginx\n"
                "\n"
                "# The load balancer health check targets /health.\n"
                "mkdir -p /usr/share/nginx/html\n"
                'echo "ok" > /usr/share/nginx/html/health\n'
                "\n"
                "systemctl enable --now nginx\n"
            )

        for fn in spec.of_kind(Kind.FUNCTION):
            runtime = str(fn.properties.get("runtime", "python3.12"))
            if runtime.startswith("nodejs"):
                files["lambda/index.js"] = (
                    "// Placeholder handler. Replace with your application code.\n"
                    "exports.handler = async (event) => {\n"
                    "  console.log('event', JSON.stringify(event));\n"
                    "  return {\n"
                    "    statusCode: 200,\n"
                    "    headers: { 'content-type': 'application/json' },\n"
                    "    body: JSON.stringify({ message: 'hello from terraform' }),\n"
                    "  };\n"
                    "};\n"
                )
            else:
                files["lambda/index.py"] = (
                    '"""Placeholder handler. Replace with your application code."""\n\n'
                    "import json\n\n\n"
                    "def handler(event, context):\n"
                    "    print(json.dumps(event))\n"
                    "    return {\n"
                    '        "statusCode": 200,\n'
                    '        "headers": {"content-type": "application/json"},\n'
                    '        "body": json.dumps({"message": "hello from terraform"}),\n'
                    "    }\n"
                )
            break

        files["README.md"] = self._project_readme(spec)
        return files

    def _project_readme(self, spec: InfrastructureSpec) -> str:
        lines = [
            f"# {spec.name}",
            "",
            "Terraform generated by the **AI-Driven Infrastructure Diagram and IaC "
            "Generator** from this requirement:",
            "",
            f"> {spec.prompt or '(no prompt recorded)'}",
            "",
            f"- Provider: `{spec.provider.value}`",
            f"- Region: `{spec.region}`",
            f"- Environment: `{spec.environment}`",
            f"- Availability zones: {spec.availability_zones}",
            "",
            "## Resources",
            "",
        ]
        for r in spec.resources:
            count = f" x{r.count}" if r.count > 1 else ""
            lines.append(f"- **{r.name}**{count} (`{r.id}`) - {r.origin.value}")

        lines += [
            "",
            "## Deploy",
            "",
            "```bash",
            "terraform init",
            "terraform plan",
            "terraform apply",
            "```",
            "",
            "Review `terraform.tfvars` first. Nothing here has been applied to a real "
            "account -- always read the plan before approving it.",
        ]
        if spec.assumptions:
            lines += ["", "## Assumptions made during generation", ""]
            lines += [f"- {a}" for a in spec.assumptions]
        if spec.warnings:
            lines += ["", "## Warnings", ""]
            lines += [f"- {w}" for w in spec.warnings]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # scaffolding
    # ------------------------------------------------------------------

    def _versions(self, spec: InfrastructureSpec) -> str:
        f = HclFile(
            "Generated by the AI-Driven Infrastructure Diagram and IaC Generator.\n"
            "Provider and version constraints."
        )
        tf = f.add(Block("terraform"))
        tf.set("required_version", TERRAFORM_VERSION)
        providers = tf.block("required_providers")
        providers.set("aws", {"source": "hashicorp/aws", "version": AWS_PROVIDER_VERSION})
        providers.set("random", {"source": "hashicorp/random", "version": "~> 3.6"})
        if spec.has(Kind.FUNCTION):
            providers.set("archive", {"source": "hashicorp/archive", "version": "~> 2.4"})

        provider = f.add(Block("provider", "aws"))
        provider.set("region", var("region"))
        default_tags = provider.block("default_tags")
        default_tags.set("tags", Raw("local.tags"))
        return f.render()

    def _variables(self, spec: InfrastructureSpec) -> str:
        f = HclFile("Input variables. Override them in terraform.tfvars.")

        f.variable("project_name", "Name prefix applied to every resource.").set_all(
            {"type": Raw("string"), "default": spec.name}
        )
        f.variable("environment", "Deployment environment (dev, staging, prod).").set_all(
            {"type": Raw("string"), "default": spec.environment}
        )
        f.variable("region", "AWS region to deploy into.").set_all(
            {"type": Raw("string"), "default": spec.region}
        )

        if spec.has(Kind.VPC):
            vpc = spec.first(Kind.VPC)
            f.variable("vpc_cidr", "CIDR block for the VPC.").set_all(
                {"type": Raw("string"),
                 "default": vpc.properties.get("cidr_block", "10.0.0.0/16")}
            )
            f.variable("az_count", "Number of availability zones to span.").set_all(
                {"type": Raw("number"), "default": spec.availability_zones}
            )

        db = spec.first(Kind.SQL_DATABASE) or spec.first(Kind.SQL_CLUSTER)
        if db is not None:
            f.variable("db_username", "Master username for the managed database.").set_all(
                {"type": Raw("string"), "default": "appadmin"}
            )
            f.variable(
                "db_name", "Initial database name."
            ).set_all({"type": Raw("string"), "default": "appdb"})

        if spec.of_kind(Kind.VM, Kind.AUTOSCALING_GROUP, Kind.BASTION):
            f.variable("key_pair_name", "Existing EC2 key pair for SSH access.").set_all(
                {"type": Raw("string"), "default": Raw("null")}
            )

        if spec.has(Kind.CONTAINER_SERVICE):
            f.variable("container_image", "Container image the ECS task runs.").set_all(
                {"type": Raw("string"), "default": "public.ecr.aws/nginx/nginx:latest"}
            )

        if spec.has(Kind.CERTIFICATE) and not spec.has(Kind.DNS_ZONE):
            # A DNS zone declares this variable itself; declaring it twice
            # would be a duplicate definition.
            f.variable("domain_name", "Domain the certificate is issued for.").set_all(
                {"type": Raw("string"), "default": "example.com"}
            )

        f.variable("tags", "Extra tags merged into every resource.").set_all(
            {"type": Raw("map(string)"), "default": {}}
        )
        return f.render()

    def _locals(self, spec: InfrastructureSpec) -> str:
        f = HclFile("Shared naming and tagging.")
        block = f.add(Block("locals"))
        block.set("name_prefix", Raw('"${var.project_name}-${var.environment}"'))
        # Trimmed prefix for resources AWS caps at 32 characters. substr is
        # safe on shorter strings, and trimsuffix stops a trailing hyphen when
        # the cut lands on one -- AWS rejects names that end in "-".
        block.set(
            "name_short",
            Raw(
                'trimsuffix(substr("${var.project_name}-${var.environment}", 0, '
                f'{TIGHTEST_LIMIT - MAX_SUFFIX}), "-")'
            ),
        )
        block.set(
            "tags",
            Raw(
                "merge({\n"
                '    Project     = var.project_name\n'
                "    Environment = var.environment\n"
                '    ManagedBy   = "terraform"\n'
                '    GeneratedBy = "ai-infra-iac-generator"\n'
                "  }, var.tags)"
            ),
        )
        if spec.has(Kind.VPC):
            f.data("aws_availability_zones", "available").set("state", "available")
        instances = spec.of_kind(Kind.VM, Kind.AUTOSCALING_GROUP, Kind.BASTION)
        if instances:
            # The AMI follows the operating system the user named. Defaulting
            # to Amazon Linux when they asked for Ubuntu would silently give
            # them a different machine than the one they described.
            props = instances[0].properties
            os_name = str(props.get("os", "amazon linux"))
            ami = f.data("aws_ami", "os",
                         f"Latest {os_name} AMI in the target region.")
            ami.set("most_recent", True)
            ami.set("owners", [str(props.get("ami_owner", "amazon"))])
            filt = ami.block("filter")
            filt.set("name", "name")
            filt.set("values", [str(props.get("ami_name_filter", "al2023-ami-*-x86_64"))])
            virt = ami.block("filter")
            virt.set("name", "virtualization-type")
            virt.set("values", ["hvm"])
        return f.render()

    def _tfvars(self, spec: InfrastructureSpec) -> str:
        lines = [
            "# Values for this deployment. Edit before running `terraform apply`.",
            f'project_name = "{spec.name}"',
            f'environment  = "{spec.environment}"',
            f'region       = "{spec.region}"',
        ]
        if spec.has(Kind.VPC):
            vpc = spec.first(Kind.VPC)
            lines.append(f'vpc_cidr     = "{vpc.properties.get("cidr_block", "10.0.0.0/16")}"')
            lines.append(f"az_count     = {spec.availability_zones}")
        if spec.has(Kind.SQL_DATABASE) or spec.has(Kind.SQL_CLUSTER):
            lines.append('db_username  = "appadmin"')
            lines.append('db_name      = "appdb"')
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # network
    # ------------------------------------------------------------------

    def _network(self, spec: InfrastructureSpec) -> HclFile:
        f = HclFile("Networking: VPC, subnets, gateways and routing.")
        vpc = spec.first(Kind.VPC)
        if vpc is None:
            # An Elastic IP is a regional resource and does not need a VPC, so
            # it still has to be emitted when there is nothing else to build.
            self._elastic_ip(f, spec)
            return f

        if vpc.is_external:
            # The VPC already exists: read it rather than creating a second one
            # alongside the user's.
            lookup = f.data("aws_vpc", _name(vpc),
                            "Existing VPC named in the requirement.")
            lookup.set("id", vpc.external_id)
        else:
            b = f.resource("aws_vpc", _name(vpc))
            b.set("cidr_block", var("vpc_cidr"))
            b.set("enable_dns_support", True)
            b.set("enable_dns_hostnames", True)
            b.set("tags", _tags(("Name", "${local.name_prefix}-vpc")))

        vpc_id = _vpc_id(spec)

        public = spec.first(Kind.SUBNET_PUBLIC)
        private = spec.first(Kind.SUBNET_PRIVATE)

        if public is not None and public.is_external:
            lookup = f.data("aws_subnets", _name(public),
                            "Existing public subnets named in the requirement.")
            filt = lookup.block("filter")
            filt.set("name", "vpc-id")
            filt.set("values", [vpc_id])
        elif public is not None:
            s = f.resource("aws_subnet", _name(public),
                           "One public subnet per availability zone.")
            s.set("count", var("az_count"))
            s.set("vpc_id", vpc_id)
            s.set("cidr_block", Raw(f"cidrsubnet({_vpc_cidr(spec).expr}, 8, count.index)"))
            s.set("availability_zone",
                  Raw("data.aws_availability_zones.available.names[count.index]"))
            s.set("map_public_ip_on_launch", True)
            s.set("tags", _tags(
                ("Name", "${local.name_prefix}-public-${count.index + 1}"),
                ("Tier", "public"),
            ))

        if private is not None and private.is_external:
            lookup = f.data("aws_subnets", _name(private),
                            "Existing private subnets named in the requirement.")
            filt = lookup.block("filter")
            filt.set("name", "vpc-id")
            filt.set("values", [vpc_id])
        elif private is not None:
            s = f.resource("aws_subnet", _name(private),
                           "One private subnet per availability zone.")
            s.set("count", var("az_count"))
            s.set("vpc_id", vpc_id)
            s.set("cidr_block", Raw(f"cidrsubnet({_vpc_cidr(spec).expr}, 8, count.index + 10)"))
            s.set("availability_zone",
                  Raw("data.aws_availability_zones.available.names[count.index]"))
            s.set("map_public_ip_on_launch", False)
            s.set("tags", _tags(
                ("Name", "${local.name_prefix}-private-${count.index + 1}"),
                ("Tier", "private"),
            ))

        igw = spec.first(Kind.INTERNET_GATEWAY)
        if igw is not None:
            b = f.resource("aws_internet_gateway", _name(igw))
            b.set("vpc_id", vpc_id)
            b.set("tags", _tags(("Name", "${local.name_prefix}-igw")))

        # Route tables follow the IR, not the gateway. A route table in the
        # spec that emitted no HCL would be a resource the diagram shows and
        # the code does not create.
        public_rt = next(
            (r for r in spec.of_kind(Kind.ROUTE_TABLE)
             if r.properties.get("scope") != "private"),
            None,
        )
        if public_rt is not None:
            rt = f.resource("aws_route_table", "public")
            rt.set("vpc_id", vpc_id)
            if igw is not None:
                route = rt.block("route")
                route.set("cidr_block", "0.0.0.0/0")
                route.set("gateway_id", ref("aws_internet_gateway", _name(igw), "id"))
            rt.set("tags", _tags(("Name", "${local.name_prefix}-public-rt")))

            if public is not None:
                assoc = f.resource("aws_route_table_association", "public")
                assoc.set("count", var("az_count"))
                assoc.set("subnet_id", _subnet_id(spec, public, "count.index"))
                assoc.set("route_table_id", ref("aws_route_table", "public", "id"))

        nat = spec.first(Kind.NAT_GATEWAY)
        eip = spec.first(Kind.ELASTIC_IP)
        nat_eip = eip if (eip is not None and eip.properties.get("attached_to") ==
                          (nat.id if nat else None)) else None

        if nat is not None and public is not None:
            if nat_eip is not None:
                e = f.resource("aws_eip", _name(nat_eip),
                               "Elastic IP for each NAT gateway.")
                e.set("count", Raw(str(nat.count)))
                e.set("domain", "vpc")
                e.set("tags", _tags(
                    ("Name", "${local.name_prefix}-nat-eip-${count.index + 1}")
                ))

            n = f.resource("aws_nat_gateway", _name(nat))
            n.set("count", Raw(str(nat.count)))
            if nat_eip is not None:
                n.set("allocation_id", Raw(f"aws_eip.{_name(nat_eip)}[count.index].id"))
            n.set("subnet_id", _subnet_id(spec, public, "count.index"))
            n.set("tags", _tags(("Name", "${local.name_prefix}-nat-${count.index + 1}")))
            n.set("depends_on", [Raw(f"aws_internet_gateway.{_name(igw)}")] if igw else [])

        # The private route table exists whenever private subnets do. A private
        # database needs no NAT, but its subnets still need somewhere to route.
        if private is not None:
            rt = f.resource("aws_route_table", "private")
            rt.set("count", var("az_count"))
            rt.set("vpc_id", vpc_id)
            if nat is not None:
                route = rt.block("route")
                route.set("cidr_block", "0.0.0.0/0")
                route.set(
                    "nat_gateway_id",
                    Raw(f"aws_nat_gateway.{_name(nat)}[count.index % {nat.count}].id"),
                )
            rt.set("tags", _tags(
                ("Name", "${local.name_prefix}-private-rt-${count.index + 1}")
            ))

            assoc = f.resource("aws_route_table_association", "private")
            assoc.set("count", var("az_count"))
            assoc.set("subnet_id", _subnet_id(spec, private, "count.index"))
            assoc.set("route_table_id", Raw("aws_route_table.private[count.index].id"))

        if nat_eip is None:
            self._elastic_ip(f, spec)

        return f

    @staticmethod
    def _elastic_ip(f: HclFile, spec: InfrastructureSpec) -> None:
        """A standalone Elastic IP.

        Not the same thing as a NAT gateway's address: this is a fixed public
        address the user asked to put on an instance.
        """
        eip = spec.first(Kind.ELASTIC_IP)
        if eip is None:
            return
        target = spec.get(str(eip.properties.get("attached_to", "")))
        e = f.resource("aws_eip", _name(eip), "Static public address.")
        e.set("domain", "vpc")
        if target is not None and target.kind is Kind.VM:
            # A counted resource is a list, so it cannot be referenced bare.
            # One Elastic IP attaches to the first instance; attaching one per
            # instance would be a resource the user never asked for.
            suffix = "[0]" if target.count > 1 else ""
            e.set("instance", Raw(f"aws_instance.{_name(target)}{suffix}.id"))
        e.set("tags", _tags(("Name", "${local.name_prefix}-eip")))

    # ------------------------------------------------------------------
    # security groups
    # ------------------------------------------------------------------

    def _security(self, spec: InfrastructureSpec) -> HclFile:
        f = HclFile("Security groups. Ingress is least-privilege by construction: "
                    "each tier only accepts traffic from the tier in front of it.")
        vpc = spec.first(Kind.VPC)
        if vpc is None:
            return f
        vpc_id = _vpc_id(spec)

        for sg in spec.of_kind(Kind.SECURITY_GROUP):
            if sg.is_external:
                lookup = f.data("aws_security_group", _name(sg),
                                "Existing security group named in the requirement.")
                lookup.set("id", sg.external_id)
                continue
            b = f.resource("aws_security_group", _name(sg))
            b.set("name", _prefixed(sg.id.replace("_", "-")))
            b.set("description", f"{sg.name} generated from the requirement description")
            b.set("vpc_id", vpc_id)

            source = sg.properties.get("ingress_from", "0.0.0.0/0")
            ports = sg.properties.get("ingress_ports", [443])
            for port in ports:
                ing = b.block("ingress")
                ing.set("description", f"Allow {port} from {source}")
                ing.set("from_port", port)
                ing.set("to_port", port)
                ing.set("protocol", "tcp")
                if source in ("0.0.0.0/0",):
                    ing.set("cidr_blocks", ["0.0.0.0/0"])
                elif source == "vpc":
                    ing.set("cidr_blocks", [_vpc_cidr(spec)])
                elif spec.get(source) is not None:
                    ing.set("security_groups", [ref("aws_security_group", source, "id")])
                else:
                    ing.set("cidr_blocks", [_vpc_cidr(spec)])

            egress = b.block("egress")
            egress.set("description", "Allow all outbound traffic")
            egress.set("from_port", 0)
            egress.set("to_port", 0)
            egress.set("protocol", "-1")
            egress.set("cidr_blocks", ["0.0.0.0/0"])

            b.set("tags", _tags(("Name", f"${{local.name_prefix}}-{sg.id.replace('_', '-')}")))

            lifecycle = b.block("lifecycle")
            lifecycle.set("create_before_destroy", True)

        return f

    # ------------------------------------------------------------------
    # compute
    # ------------------------------------------------------------------

    def _compute(self, spec: InfrastructureSpec) -> HclFile:
        f = HclFile("Compute resources.")
        private = spec.first(Kind.SUBNET_PRIVATE)
        public = spec.first(Kind.SUBNET_PUBLIC)
        app_subnet = private or public

        for vm in spec.of_kind(Kind.VM):
            b = f.resource("aws_instance", _name(vm))
            if vm.count > 1:
                b.set("count", vm.count)
            b.set("ami", ref("data", "aws_ami", "os", "id"))
            b.set("instance_type", vm.properties.get("instance_type", "t3.micro"))
            if app_subnet is not None:
                index = "count.index" if vm.count > 1 else "0"
                b.set("subnet_id", _subnet_id(spec, app_subnet, index))
            sg = spec.get("app_sg")
            if sg is not None:
                b.set("vpc_security_group_ids", [ref("aws_security_group", "app_sg", "id")])
            if spec.get("instance_role") is not None:
                b.set("iam_instance_profile", ref("aws_iam_instance_profile", "instance", "name"))
            b.set("key_name", var("key_pair_name"))
            b.set("user_data", Raw("file(\"${path.module}/user_data.sh\")"))
            root = b.block("root_block_device")
            root.set("volume_size", vm.properties.get("volume_size", 20))
            root.set("volume_type", "gp3")
            root.set("encrypted", True)
            suffix = "-${count.index + 1}" if vm.count > 1 else ""
            b.set("tags", _named_tags(vm, f"${{local.name_prefix}}-app{suffix}"))

        for bastion in spec.of_kind(Kind.BASTION):
            b = f.resource("aws_instance", _name(bastion))
            b.set("ami", ref("data", "aws_ami", "os", "id"))
            b.set("instance_type", bastion.properties.get("instance_type", "t3.micro"))
            if public is not None:
                b.set("subnet_id", _subnet_id(spec, public, "0"))
            b.set("associate_public_ip_address", True)
            if spec.get("bastion_sg") is not None:
                b.set("vpc_security_group_ids", [ref("aws_security_group", "bastion_sg", "id")])
            b.set("key_name", var("key_pair_name"))
            b.set("tags", _tags(("Name", "${local.name_prefix}-bastion")))

        asg = spec.first(Kind.AUTOSCALING_GROUP)
        if asg is not None:
            lt = f.resource("aws_launch_template", "app")
            lt.set("name_prefix", Raw('"${local.name_prefix}-lt-"'))
            lt.set("image_id", ref("data", "aws_ami", "os", "id"))
            lt.set("instance_type", asg.properties.get("instance_type", "t3.micro"))
            lt.set("key_name", var("key_pair_name"))
            lt.set("user_data", Raw('filebase64("${path.module}/user_data.sh")'))
            if spec.get("app_sg") is not None:
                lt.set("vpc_security_group_ids", [ref("aws_security_group", "app_sg", "id")])
            if spec.get("instance_role") is not None:
                profile = lt.block("iam_instance_profile")
                profile.set("name", ref("aws_iam_instance_profile", "instance", "name"))
            mon = lt.block("monitoring")
            mon.set("enabled", True)
            tag_spec = lt.block("tag_specifications")
            tag_spec.set("resource_type", "instance")
            tag_spec.set("tags", _tags(("Name", "${local.name_prefix}-app")))

            g = f.resource("aws_autoscaling_group", _name(asg))
            g.set("name", _prefixed("asg"))
            g.set("min_size", asg.properties.get("min_size", 1))
            g.set("max_size", asg.properties.get("max_size", 4))
            g.set("desired_capacity", asg.properties.get("desired_capacity", 2))
            if app_subnet is not None:
                g.set("vpc_zone_identifier", _subnet_ids(spec, app_subnet))
            g.set("health_check_type", "ELB" if spec.has(Kind.LOAD_BALANCER) else "EC2")
            g.set("health_check_grace_period", 300)
            if spec.has(Kind.TARGET_GROUP):
                g.set("target_group_arns", [ref("aws_lb_target_group", "app_tg", "arn")])
            template = g.block("launch_template")
            template.set("id", ref("aws_launch_template", "app", "id"))
            template.set("version", Raw('"$Latest"'))
            tag = g.block("tag")
            tag.set("key", "Name")
            tag.set("value", Raw('"${local.name_prefix}-app"'))
            tag.set("propagate_at_launch", True)

            policy = f.resource("aws_autoscaling_policy", "cpu_target",
                                "Target tracking keeps average CPU near 60%.")
            policy.set("name", _prefixed("cpu-target"))
            policy.set("autoscaling_group_name", ref("aws_autoscaling_group", _name(asg), "name"))
            policy.set("policy_type", "TargetTrackingScaling")
            tracking = policy.block("target_tracking_configuration")
            spec_block = tracking.block("predefined_metric_specification")
            spec_block.set("predefined_metric_type", "ASGAverageCPUUtilization")
            tracking.set("target_value", 60)

        for fn in spec.of_kind(Kind.FUNCTION):
            archive = f.data("archive_file", _name(fn),
                             "Package the handler source at plan time -- no build step needed.")
            archive.set("type", "zip")
            archive.set("source_dir", Raw('"${path.module}/lambda"'))
            archive.set("output_path", Raw(f'"${{path.module}}/build/{_name(fn)}.zip"'))

            b = f.resource("aws_lambda_function", _name(fn))
            b.set("function_name", _prefixed(fn.id.replace("_", "-")))
            b.set("role", ref("aws_iam_role", "lambda_role", "arn"))
            b.set("handler", fn.properties.get("handler", "index.handler"))
            b.set("runtime", fn.properties.get("runtime", "python3.12"))
            b.set("memory_size", fn.properties.get("memory_size", 512))
            b.set("timeout", fn.properties.get("timeout", 30))
            b.set("filename", ref("data", "archive_file", _name(fn), "output_path"))
            b.set("source_code_hash",
                  ref("data", "archive_file", _name(fn), "output_base64sha256"))
            env = b.block("environment")
            env_vars: dict[str, object] = {"ENVIRONMENT": var("environment")}
            table = spec.first(Kind.NOSQL_TABLE)
            if table is not None:
                env_vars["TABLE_NAME"] = ref("aws_dynamodb_table", _name(table), "name")
            queue = spec.first(Kind.QUEUE)
            if queue is not None:
                env_vars["QUEUE_URL"] = ref("aws_sqs_queue", _name(queue), "url")
            env.set("variables", env_vars)
            b.set("tags", _tags())

            log_group = f.resource("aws_cloudwatch_log_group", f"{_name(fn)}_logs")
            log_group.set("name", Raw(
                f'"/aws/lambda/${{aws_lambda_function.{_name(fn)}.function_name}}"'
            ))
            log_group.set("retention_in_days", 14)
            log_group.set("tags", _tags())

        ecs = spec.first(Kind.CONTAINER_SERVICE)
        if ecs is not None:
            cluster = f.resource("aws_ecs_cluster", "main")
            cluster.set("name", _prefixed("cluster"))
            settings = cluster.block("setting")
            settings.set("name", "containerInsights")
            settings.set("value", "enabled")
            cluster.set("tags", _tags())

            task = f.resource("aws_ecs_task_definition", "app")
            task.set("family", _prefixed("app"))
            task.set("network_mode", "awsvpc")
            task.set("requires_compatibilities", ["FARGATE"])
            task.set("cpu", str(ecs.properties.get("cpu", 512)))
            task.set("memory", str(ecs.properties.get("memory", 1024)))
            task.set("execution_role_arn", ref("aws_iam_role", "task_role", "arn"))
            task.set("container_definitions", Raw(
                'jsonencode([{\n'
                '    name      = "app"\n'
                '    image     = var.container_image\n'
                '    essential = true\n'
                '    portMappings = [{ containerPort = 80, protocol = "tcp" }]\n'
                '    logConfiguration = {\n'
                '      logDriver = "awslogs"\n'
                '      options = {\n'
                '        awslogs-group         = aws_cloudwatch_log_group.ecs.name\n'
                '        awslogs-region        = var.region\n'
                '        awslogs-stream-prefix = "app"\n'
                '      }\n'
                '    }\n'
                '  }])'
            ))
            task.set("tags", _tags())

            logs = f.resource("aws_cloudwatch_log_group", "ecs")
            logs.set("name", Raw('"/ecs/${local.name_prefix}"'))
            logs.set("retention_in_days", 14)

            svc = f.resource("aws_ecs_service", _name(ecs))
            svc.set("name", _prefixed("service"))
            svc.set("cluster", ref("aws_ecs_cluster", "main", "id"))
            svc.set("task_definition", ref("aws_ecs_task_definition", "app", "arn"))
            svc.set("desired_count", ecs.properties.get("desired_count", 2))
            svc.set("launch_type", "FARGATE")
            net = svc.block("network_configuration")
            if app_subnet is not None:
                net.set("subnets", _subnet_ids(spec, app_subnet))
            if spec.get("app_sg") is not None:
                net.set("security_groups", [ref("aws_security_group", "app_sg", "id")])
            net.set("assign_public_ip", False)
            if spec.has(Kind.TARGET_GROUP):
                lb_block = svc.block("load_balancer")
                lb_block.set("target_group_arn", ref("aws_lb_target_group", "app_tg", "arn"))
                lb_block.set("container_name", "app")
                lb_block.set("container_port", 80)
            svc.set("tags", _tags())

        eks = spec.first(Kind.KUBERNETES_CLUSTER)
        if eks is not None:
            c = f.resource("aws_eks_cluster", _name(eks))
            c.set("name", _prefixed("eks"))
            c.set("role_arn", ref("aws_iam_role", "cluster_role", "arn"))
            c.set("version", eks.properties.get("version", "1.29"))
            vpc_cfg = c.block("vpc_config")
            if app_subnet is not None:
                vpc_cfg.set("subnet_ids", _subnet_ids(spec, app_subnet))
            vpc_cfg.set("endpoint_private_access", True)
            vpc_cfg.set("endpoint_public_access", True)
            c.set("tags", _tags())

            ng = f.resource("aws_eks_node_group", "default")
            ng.set("cluster_name", ref("aws_eks_cluster", _name(eks), "name"))
            ng.set("node_group_name", _prefixed("ng"))
            ng.set("node_role_arn", ref("aws_iam_role", "eks_node", "arn"))
            if app_subnet is not None:
                ng.set("subnet_ids", _subnet_ids(spec, app_subnet))
            ng.set("instance_types", [eks.properties.get("node_instance_type", "t3.medium")])
            scaling = ng.block("scaling_config")
            node_count = eks.properties.get("node_count", 2)
            scaling.set("desired_size", node_count)
            scaling.set("min_size", max(1, node_count - 1))
            scaling.set("max_size", node_count * 2)
            ng.set("tags", _tags())

        registry = spec.first(Kind.CONTAINER_REGISTRY)
        if registry is not None:
            r = f.resource("aws_ecr_repository", _name(registry))
            r.set("name", _prefixed("app"))
            r.set("image_tag_mutability", "IMMUTABLE")
            scan = r.block("image_scanning_configuration")
            scan.set("scan_on_push", True)
            r.set("tags", _tags())

        return f

    # ------------------------------------------------------------------
    # data stores
    # ------------------------------------------------------------------

    def _data(self, spec: InfrastructureSpec) -> HclFile:
        f = HclFile("Data stores.")
        private = spec.first(Kind.SUBNET_PRIVATE)

        databases = spec.of_kind(Kind.SQL_DATABASE)
        if databases and private is not None:
            # One subnet group serves every database in the VPC.
            sn = f.resource("aws_db_subnet_group", "main")
            sn.set("name", _prefixed("db-subnets"))
            sn.set("subnet_ids", _subnet_ids(spec, private))
            sn.set("tags", _tags())

        for db in databases:
            # Named per database, so two databases get two credentials rather
            # than silently sharing one.
            password = f"{_name(db)}_password"
            pw = f.resource("random_password", password,
                            "Master password, generated rather than written down.")
            pw.set("length", 32)
            pw.set("special", True)
            pw.set("override_special", "!#$%&*()-_=+[]{}<>:?")

            b = f.resource("aws_db_instance", _name(db))
            b.set("identifier", _prefixed(db.id.replace("_", "-")))
            b.set("engine", db.properties.get("engine", "postgres"))
            b.set("engine_version", db.properties.get("engine_version", "15.5"))
            b.set("instance_class", db.properties.get("instance_class", "db.t3.micro"))
            b.set("allocated_storage", db.properties.get("allocated_storage", 20))
            b.set("storage_type", "gp3")
            b.set("storage_encrypted", True)
            b.set("db_name", var("db_name"))
            b.set("username", var("db_username"))
            b.set("password", Raw(f"random_password.{password}.result"))
            b.set("port", db.properties.get("port", 5432))
            if private is not None:
                b.set("db_subnet_group_name", ref("aws_db_subnet_group", "main", "name"))
            if spec.get("db_sg") is not None:
                b.set("vpc_security_group_ids", [ref("aws_security_group", "db_sg", "id")])
            b.set("multi_az", bool(db.properties.get("multi_az", False)))
            b.set("publicly_accessible", False)
            b.set("backup_retention_period", db.properties.get("backup_retention_period", 7))
            b.set("deletion_protection", spec.environment == "prod")
            b.set("skip_final_snapshot", spec.environment != "prod")
            b.set("auto_minor_version_upgrade", True)
            b.set("performance_insights_enabled", spec.environment == "prod")
            b.set("tags", _tags(("Name", "${local.name_prefix}-db")))

        for table in spec.of_kind(Kind.NOSQL_TABLE):
            b = f.resource("aws_dynamodb_table", _name(table))
            b.set("name", _prefixed(table.id.replace("_", "-")))
            b.set("billing_mode", table.properties.get("billing_mode", "PAY_PER_REQUEST"))
            hash_key = table.properties.get("hash_key", "id")
            b.set("hash_key", hash_key)
            attr = b.block("attribute")
            attr.set("name", hash_key)
            attr.set("type", "S")
            pitr = b.block("point_in_time_recovery")
            pitr.set("enabled", bool(table.properties.get("point_in_time_recovery", False)))
            sse = b.block("server_side_encryption")
            sse.set("enabled", True)
            b.set("tags", _tags())

        cache = spec.first(Kind.CACHE)
        if cache is not None:
            if private is not None:
                sn = f.resource("aws_elasticache_subnet_group", "main")
                sn.set("name", _prefixed("cache-subnets"))
                sn.set("subnet_ids", _subnet_ids(spec, private))

            b = f.resource("aws_elasticache_replication_group", _name(cache))
            b.set("replication_group_id", _capped("redis"))
            b.set("description", "Redis cache generated from the requirement description")
            b.set("engine", "redis")
            b.set("node_type", cache.properties.get("node_type", "cache.t3.micro"))
            b.set("num_cache_clusters", cache.properties.get("num_nodes", 1))
            b.set("automatic_failover_enabled", cache.properties.get("num_nodes", 1) > 1)
            b.set("port", 6379)
            if private is not None:
                b.set("subnet_group_name", ref("aws_elasticache_subnet_group", "main", "name"))
            if spec.get("cache_sg") is not None:
                b.set("security_group_ids", [ref("aws_security_group", "cache_sg", "id")])
            b.set("at_rest_encryption_enabled", True)
            b.set("transit_encryption_enabled", True)
            b.set("tags", _tags())

        for bucket in spec.of_kind(Kind.OBJECT_STORAGE):
            b = f.resource("aws_s3_bucket", _name(bucket))
            # A stated bucket name is used verbatim: S3 names are global and
            # the user asking for one means they have chosen it.
            if bucket.display_name:
                b.set("bucket", bucket.display_name)
            else:
                b.set("bucket", Raw(
                    f'"${{local.name_prefix}}-{bucket.id.replace("_", "-")}-'
                    '${random_id.bucket_suffix.hex}"'
                ))
            b.set("tags", _tags())

            ver = f.resource("aws_s3_bucket_versioning", f"{_name(bucket)}_versioning")
            ver.set("bucket", ref("aws_s3_bucket", _name(bucket), "id"))
            vc = ver.block("versioning_configuration")
            vc.set("status", "Enabled" if bucket.properties.get("versioning") else "Suspended")

            enc = f.resource("aws_s3_bucket_server_side_encryption_configuration",
                             f"{_name(bucket)}_encryption")
            enc.set("bucket", ref("aws_s3_bucket", _name(bucket), "id"))
            rule = enc.block("rule")
            default = rule.block("apply_server_side_encryption_by_default")
            default.set("sse_algorithm", "AES256")

            access = f.resource("aws_s3_bucket_public_access_block",
                                f"{_name(bucket)}_access")
            access.set("bucket", ref("aws_s3_bucket", _name(bucket), "id"))
            blocked = not bucket.properties.get("public_read", False)
            access.set("block_public_acls", blocked)
            access.set("block_public_policy", blocked)
            access.set("ignore_public_acls", blocked)
            access.set("restrict_public_buckets", blocked)

        if spec.has(Kind.OBJECT_STORAGE):
            rid = f.resource("random_id", "bucket_suffix",
                             "S3 bucket names are globally unique; add a stable suffix.")
            rid.set("byte_length", 4)

        for cluster in spec.of_kind(Kind.SQL_CLUSTER):
            # Aurora is not an aws_db_instance. Storage is shared across the
            # cluster and the writer/reader instances are separate resources;
            # putting an Aurora engine on aws_db_instance passes `terraform
            # validate` and is then rejected by the API at apply time.
            if private is not None and not spec.has(Kind.SQL_DATABASE):
                sn = f.resource("aws_db_subnet_group", "main")
                sn.set("name", _prefixed("db-subnets"))
                sn.set("subnet_ids", _subnet_ids(spec, private))
                sn.set("tags", _tags())

            pw = f.resource("random_password", f"{_name(cluster)}_master",
                            "Master password for the cluster.")
            pw.set("length", 32)
            pw.set("special", False)

            b = f.resource("aws_rds_cluster", _name(cluster))
            b.set("cluster_identifier", _prefixed(cluster.id.replace("_", "-")))
            b.set("engine", cluster.properties.get("engine", "aurora-postgresql"))
            b.set("engine_version", cluster.properties.get("engine_version", "15.4"))
            b.set("database_name", var("db_name"))
            b.set("master_username", var("db_username"))
            b.set("master_password", Raw(f"random_password.{_name(cluster)}_master.result"))
            b.set("port", cluster.properties.get("port", 5432))
            if private is not None:
                b.set("db_subnet_group_name", ref("aws_db_subnet_group", "main", "name"))
            if spec.get("db_sg") is not None:
                b.set("vpc_security_group_ids", [ref("aws_security_group", "db_sg", "id")])
            b.set("storage_encrypted", True)
            b.set("backup_retention_period",
                  cluster.properties.get("backup_retention_period", 7))
            b.set("preferred_backup_window", "03:00-04:00")
            b.set("deletion_protection", spec.environment == "prod")
            b.set("skip_final_snapshot", spec.environment != "prod")
            b.set("tags", _tags(("Name", "${local.name_prefix}-aurora")))

            instances = max(1, int(cluster.properties.get("instances", 1)))
            inst = f.resource("aws_rds_cluster_instance", f"{_name(cluster)}_instances",
                              "Writer plus any readers. Aurora shares one storage volume.")
            inst.set("count", instances)
            inst.set("identifier",
                     Raw('"${local.name_prefix}-aurora-${count.index + 1}"'))
            inst.set("cluster_identifier", ref("aws_rds_cluster", _name(cluster), "id"))
            inst.set("instance_class",
                     cluster.properties.get("instance_class", "db.t3.medium"))
            inst.set("engine", ref("aws_rds_cluster", _name(cluster), "engine"))
            inst.set("engine_version", ref("aws_rds_cluster", _name(cluster), "engine_version"))
            inst.set("performance_insights_enabled", spec.environment == "prod")
            inst.set("tags", _tags())

        warehouse = spec.first(Kind.DATA_WAREHOUSE)
        if warehouse is not None:
            if private is not None:
                sn = f.resource("aws_redshift_subnet_group", "main")
                sn.set("name", _prefixed("redshift-subnets"))
                sn.set("subnet_ids", _subnet_ids(spec, private))
                sn.set("tags", _tags())

            pw = f.resource("random_password", "redshift")
            pw.set("length", 32)
            pw.set("special", False)

            b = f.resource("aws_redshift_cluster", _name(warehouse))
            b.set("cluster_identifier", _prefixed("warehouse"))
            b.set("database_name", warehouse.properties.get("database_name", "analytics"))
            b.set("master_username", "rsadmin")
            b.set("master_password", Raw("random_password.redshift.result"))
            b.set("node_type", warehouse.properties.get("node_type", "ra3.xlplus"))
            nodes = int(warehouse.properties.get("nodes", 1))
            b.set("cluster_type", "multi-node" if nodes > 1 else "single-node")
            if nodes > 1:
                b.set("number_of_nodes", nodes)
            if private is not None:
                b.set("cluster_subnet_group_name",
                      ref("aws_redshift_subnet_group", "main", "name"))
            if spec.get("warehouse_sg") is not None:
                b.set("vpc_security_group_ids",
                      [ref("aws_security_group", "warehouse_sg", "id")])
            b.set("encrypted", True)
            b.set("publicly_accessible", False)
            b.set("skip_final_snapshot", spec.environment != "prod")
            b.set("tags", _tags(("Name", "${local.name_prefix}-warehouse")))

        efs = spec.first(Kind.FILE_STORAGE)
        if efs is not None:
            b = f.resource("aws_efs_file_system", _name(efs))
            b.set("creation_token", _prefixed("efs"))
            b.set("encrypted", True)
            b.set("performance_mode", "generalPurpose")
            lifecycle = b.block("lifecycle_policy")
            lifecycle.set("transition_to_ia", "AFTER_30_DAYS")
            b.set("tags", _tags(("Name", "${local.name_prefix}-efs")))

            if private is not None:
                mt = f.resource("aws_efs_mount_target", "main")
                mt.set("count", var("az_count"))
                mt.set("file_system_id", ref("aws_efs_file_system", _name(efs), "id"))
                mt.set("subnet_id", _subnet_id(spec, private, "count.index"))
                if spec.get("app_sg") is not None:
                    mt.set("security_groups", [ref("aws_security_group", "app_sg", "id")])

        return f

    # ------------------------------------------------------------------
    # edge / traffic
    # ------------------------------------------------------------------

    def _edge(self, spec: InfrastructureSpec) -> HclFile:
        f = HclFile("Traffic entry points: load balancing, API, CDN and DNS.")
        public = spec.first(Kind.SUBNET_PUBLIC)
        private = spec.first(Kind.SUBNET_PRIVATE)
        vpc = spec.first(Kind.VPC)

        # The three balancer types differ in more than a type string: the
        # listener protocol, the health check shape, the target group protocol
        # and whether a security group attaches all follow from the choice.
        balancers = spec.of_kind(
            Kind.LOAD_BALANCER, Kind.NETWORK_LOAD_BALANCER, Kind.GATEWAY_LOAD_BALANCER
        )
        lb = spec.first(Kind.LOAD_BALANCER) or spec.first(Kind.NETWORK_LOAD_BALANCER)

        certificate = spec.first(Kind.CERTIFICATE)
        if certificate is not None:
            cert = f.resource(
                "aws_acm_certificate", _name(certificate),
                "DNS validation records must be published before this completes.",
            )
            cert.set("domain_name", var("domain_name"))
            cert.set("validation_method", "DNS")
            cert.set("tags", _tags())
            lifecycle = cert.block("lifecycle")
            lifecycle.set("create_before_destroy", True)

        for balancer in balancers:
            tf_kind = {
                Kind.LOAD_BALANCER: "application",
                Kind.NETWORK_LOAD_BALANCER: "network",
                Kind.GATEWAY_LOAD_BALANCER: "gateway",
            }[balancer.kind]
            suffix = {"application": "alb", "network": "nlb", "gateway": "gwlb"}[tf_kind]

            b = f.resource("aws_lb", _name(balancer))
            b.set("name", _capped(suffix))
            b.set("load_balancer_type", tf_kind)
            # A gateway load balancer is always internal; only an application
            # load balancer takes a security group.
            b.set("internal", True if tf_kind == "gateway"
                  else bool(balancer.properties.get("internal", False)))
            if tf_kind == "application" and spec.get("alb_sg") is not None:
                b.set("security_groups", [ref("aws_security_group", "alb_sg", "id")])
            if tf_kind == "gateway" and private is not None:
                b.set("subnets", _subnet_ids(spec, private))
            elif tf_kind != "gateway" and public is not None:
                b.set("subnets", _subnet_ids(spec, public))
            b.set("enable_deletion_protection", spec.environment == "prod")
            if tf_kind == "application":
                b.set("enable_http2", True)
                b.set("idle_timeout", 60)
            elif tf_kind == "network":
                b.set("enable_cross_zone_load_balancing", True)
            b.set("tags", _tags(("Name", f"${{local.name_prefix}}-{suffix}")))

        tg = spec.first(Kind.TARGET_GROUP)
        # A target group is valid on its own; only the listeners need a
        # balancer to attach to.
        if tg is not None and vpc is not None:
            protocol = str(tg.properties.get("protocol", "HTTP"))
            b = f.resource("aws_lb_target_group", _name(tg))
            b.set("name", _capped("tg"))
            b.set("port", tg.properties.get("port", 80))
            b.set("protocol", protocol)
            b.set("vpc_id", ref("aws_vpc", _name(vpc), "id"))
            b.set("target_type", "ip" if spec.has(Kind.CONTAINER_SERVICE) else "instance")
            hc = b.block("health_check")
            hc.set("enabled", True)
            if protocol in ("HTTP", "HTTPS"):
                # Only a layer 7 health check has a path or a status matcher.
                hc.set("path", tg.properties.get("health_check_path", "/health"))
                hc.set("matcher", "200-399")
            hc.set("healthy_threshold", 2)
            hc.set("unhealthy_threshold", 3)
            hc.set("interval", 30)
            b.set("tags", _tags())

            if lb is not None:
                self._listeners(f, spec, lb, tg, certificate)

        api = spec.first(Kind.API_GATEWAY)
        if api is not None:
            b = f.resource("aws_apigatewayv2_api", _name(api))
            b.set("name", _prefixed("api"))
            b.set("protocol_type", "HTTP")
            cors = b.block("cors_configuration")
            cors.set("allow_origins", ["*"])
            cors.set("allow_methods", ["GET", "POST", "PUT", "DELETE", "OPTIONS"])
            cors.set("allow_headers", ["content-type", "authorization"])
            b.set("tags", _tags())

            stage = f.resource("aws_apigatewayv2_stage", "default")
            stage.set("api_id", ref("aws_apigatewayv2_api", _name(api), "id"))
            stage.set("name", Raw('"$default"'))
            stage.set("auto_deploy", True)
            stage.set("tags", _tags())

            fn = spec.first(Kind.FUNCTION)
            if fn is not None:
                integ = f.resource("aws_apigatewayv2_integration", "lambda")
                integ.set("api_id", ref("aws_apigatewayv2_api", _name(api), "id"))
                integ.set("integration_type", "AWS_PROXY")
                integ.set("integration_uri", ref("aws_lambda_function", _name(fn), "invoke_arn"))
                integ.set("payload_format_version", "2.0")

                route = f.resource("aws_apigatewayv2_route", "default")
                route.set("api_id", ref("aws_apigatewayv2_api", _name(api), "id"))
                route.set("route_key", Raw('"$default"'))
                route.set("target", Raw('"integrations/${aws_apigatewayv2_integration.lambda.id}"'))

                perm = f.resource("aws_lambda_permission", "api_gateway")
                perm.set("statement_id", "AllowExecutionFromAPIGateway")
                perm.set("action", "lambda:InvokeFunction")
                perm.set("function_name", ref("aws_lambda_function", _name(fn), "function_name"))
                perm.set("principal", "apigateway.amazonaws.com")
                perm.set("source_arn", Raw(
                    f'"${{aws_apigatewayv2_api.{_name(api)}.execution_arn}}/*/*"'
                ))

        cdn = spec.first(Kind.CDN)
        if cdn is not None:
            b = f.resource("aws_cloudfront_distribution", _name(cdn))
            b.set("enabled", True)
            b.set("is_ipv6_enabled", True)
            b.set("comment", Raw('"${local.name_prefix} distribution"'))
            b.set("price_class", "PriceClass_100")
            b.set("default_root_object", "index.html")

            origin_type = cdn.properties.get("origin_type")
            origin = b.block("origin")
            if origin_type == "s3":
                bucket = spec.get(str(cdn.properties.get("origin")))
                if bucket is not None:
                    origin.set("domain_name",
                               ref("aws_s3_bucket", _name(bucket), "bucket_regional_domain_name"))
                origin.set("origin_id", "s3-origin")
                origin.set("origin_access_control_id",
                           ref("aws_cloudfront_origin_access_control", "s3", "id"))

                oac = f.resource("aws_cloudfront_origin_access_control", "s3")
                oac.set("name", _prefixed("oac"))
                oac.set("origin_access_control_origin_type", "s3")
                oac.set("signing_behavior", "always")
                oac.set("signing_protocol", "sigv4")
            else:
                if lb is not None:
                    origin.set("domain_name", ref("aws_lb", _name(lb), "dns_name"))
                origin.set("origin_id", "alb-origin")
                custom = origin.block("custom_origin_config")
                custom.set("http_port", 80)
                custom.set("https_port", 443)
                custom.set("origin_protocol_policy", "http-only")
                custom.set("origin_ssl_protocols", ["TLSv1.2"])

            cache = b.block("default_cache_behavior")
            cache.set("target_origin_id", "s3-origin" if origin_type == "s3" else "alb-origin")
            cache.set("viewer_protocol_policy", "redirect-to-https")
            cache.set("allowed_methods", ["GET", "HEAD", "OPTIONS"])
            cache.set("cached_methods", ["GET", "HEAD"])
            cache.set("compress", True)
            forwarded = cache.block("forwarded_values")
            forwarded.set("query_string", False)
            cookies = forwarded.block("cookies")
            cookies.set("forward", "none")
            cache.set("min_ttl", 0)
            cache.set("default_ttl", 3600)
            cache.set("max_ttl", 86400)

            restrictions = b.block("restrictions")
            geo = restrictions.block("geo_restriction")
            geo.set("restriction_type", "none")

            viewer = b.block("viewer_certificate")
            viewer.set("cloudfront_default_certificate", True)
            b.set("tags", _tags())

        dns = spec.first(Kind.DNS_ZONE)
        if dns is not None:
            b = f.resource("aws_route53_zone", _name(dns))
            b.set("name", Raw("var.domain_name"))
            b.set("comment", "Managed by the AI-Driven IaC Generator")
            b.set("tags", _tags())

            target = spec.get(str(dns.properties.get("alias_target", "")))
            if target is not None:
                rec = f.resource("aws_route53_record", "apex")
                rec.set("zone_id", ref("aws_route53_zone", _name(dns), "zone_id"))
                rec.set("name", Raw("var.domain_name"))
                rec.set("type", "A")
                alias = rec.block("alias")
                if target.kind is Kind.CDN:
                    alias.set("name", ref("aws_cloudfront_distribution", _name(target),
                                          "domain_name"))
                    alias.set("zone_id", ref("aws_cloudfront_distribution", _name(target),
                                             "hosted_zone_id"))
                elif target.kind is Kind.LOAD_BALANCER:
                    alias.set("name", ref("aws_lb", _name(target), "dns_name"))
                    alias.set("zone_id", ref("aws_lb", _name(target), "zone_id"))
                alias.set("evaluate_target_health", True)

            v = f.variable("domain_name", "Domain name for the hosted zone.")
            v.set("type", Raw("string"))
            v.set("default", "example.com")

        waf = spec.first(Kind.WAF)
        if waf is not None:
            b = f.resource("aws_wafv2_web_acl", _name(waf))
            b.set("name", _prefixed("waf"))
            b.set("scope", waf.properties.get("scope", "REGIONAL"))
            default_action = b.block("default_action")
            default_action.block("allow")
            rule = b.block("rule")
            rule.set("name", "AWSManagedRulesCommonRuleSet")
            rule.set("priority", 1)
            override = rule.block("override_action")
            override.block("none")
            statement = rule.block("statement")
            managed = statement.block("managed_rule_group_statement")
            managed.set("name", "AWSManagedRulesCommonRuleSet")
            managed.set("vendor_name", "AWS")
            rule_vis = rule.block("visibility_config")
            rule_vis.set("cloudwatch_metrics_enabled", True)
            rule_vis.set("metric_name", "common-rules")
            rule_vis.set("sampled_requests_enabled", True)
            vis = b.block("visibility_config")
            vis.set("cloudwatch_metrics_enabled", True)
            vis.set("metric_name", Raw('"${local.name_prefix}-waf"'))
            vis.set("sampled_requests_enabled", True)
            b.set("tags", _tags())

        return f

    @staticmethod
    def _listeners(
        f: HclFile,
        spec: InfrastructureSpec,
        lb: Resource,
        tg: Resource,
        certificate: Resource | None,
    ) -> None:
        """Listeners for the balancer, including TLS termination.

        When HTTPS is asked for the plain HTTP listener is not dropped: it is
        turned into a permanent redirect. Removing it would silently break
        every existing http:// link, which is not what "add HTTPS" means.
        """
        network = lb.kind is Kind.NETWORK_LOAD_BALANCER
        https = bool(lb.properties.get("https")) and certificate is not None

        if https:
            secure = f.resource("aws_lb_listener", "https")
            secure.set("load_balancer_arn", ref("aws_lb", _name(lb), "arn"))
            secure.set("port", lb.properties.get("tls_port", 443))
            secure.set("protocol", "TLS" if network else "HTTPS")
            secure.set("ssl_policy", "ELBSecurityPolicy-TLS13-1-2-2021-06")
            secure.set("certificate_arn",
                       ref("aws_acm_certificate", _name(certificate), "arn"))
            action = secure.block("default_action")
            action.set("type", "forward")
            action.set("target_group_arn", ref("aws_lb_target_group", _name(tg), "arn"))

        plain = f.resource(
            "aws_lb_listener", "http",
            "Redirects to HTTPS rather than being removed, so existing "
            "http:// links keep working." if https else None,
        )
        plain.set("load_balancer_arn", ref("aws_lb", _name(lb), "arn"))
        plain.set("port", lb.properties.get("listener_port", 80))
        plain.set("protocol", "TCP" if network else "HTTP")
        action = plain.block("default_action")
        if https and not network:
            action.set("type", "redirect")
            redirect = action.block("redirect")
            redirect.set("port", "443")
            redirect.set("protocol", "HTTPS")
            redirect.set("status_code", "HTTP_301")
        else:
            action.set("type", "forward")
            action.set("target_group_arn", ref("aws_lb_target_group", _name(tg), "arn"))

    # ------------------------------------------------------------------
    # integration
    # ------------------------------------------------------------------

    def _integration(self, spec: InfrastructureSpec) -> HclFile:
        f = HclFile("Messaging and eventing.")

        for queue in spec.of_kind(Kind.QUEUE):
            dlq = f.resource("aws_sqs_queue", f"{_name(queue)}_dlq",
                             "Dead-letter queue for messages that fail repeatedly.")
            dlq.set("name", _prefixed(f"{queue.id.replace('_', '-')}-dlq"))
            dlq.set("message_retention_seconds", 1209600)
            dlq.set("tags", _tags())

            b = f.resource("aws_sqs_queue", _name(queue))
            b.set("name", _prefixed(queue.id.replace("_", "-")))
            b.set("visibility_timeout_seconds", 60)
            b.set("message_retention_seconds", 345600)
            b.set("redrive_policy", Raw(
                "jsonencode({\n"
                f"    deadLetterTargetArn = aws_sqs_queue.{_name(queue)}_dlq.arn\n"
                "    maxReceiveCount     = 5\n"
                "  })"
            ))
            b.set("tags", _tags())

            fn = spec.first(Kind.FUNCTION)
            if fn is not None:
                mapping = f.resource("aws_lambda_event_source_mapping", f"{_name(queue)}_lambda")
                mapping.set("event_source_arn", ref("aws_sqs_queue", _name(queue), "arn"))
                mapping.set("function_name", ref("aws_lambda_function", _name(fn), "arn"))
                mapping.set("batch_size", 10)

        for topic in spec.of_kind(Kind.TOPIC):
            b = f.resource("aws_sns_topic", _name(topic))
            b.set("name", _prefixed(topic.id.replace("_", "-")))
            b.set("tags", _tags())

        for bus in spec.of_kind(Kind.EVENT_BUS):
            b = f.resource("aws_cloudwatch_event_bus", _name(bus))
            b.set("name", _prefixed("bus"))
            b.set("tags", _tags())

        for secret in spec.of_kind(Kind.SECRET_STORE):
            b = f.resource("aws_secretsmanager_secret", _name(secret))
            b.set("name", _prefixed(secret.id.replace("_", "-")))
            b.set("description", secret.properties.get("description", "Application secret"))
            b.set("recovery_window_in_days", 7)
            b.set("tags", _tags())

            if spec.has(Kind.SQL_DATABASE) and secret.id == "db_secret":
                db = spec.first(Kind.SQL_DATABASE)
                version = f.resource("aws_secretsmanager_secret_version", f"{_name(secret)}_value")
                version.set("secret_id", ref("aws_secretsmanager_secret", _name(secret), "id"))
                version.set("secret_string", Raw(
                    "jsonencode({\n"
                    "    username = var.db_username\n"
                    "    password = random_password.db.result\n"
                    f"    host     = aws_db_instance.{_name(db)}.address\n"
                    f"    port     = aws_db_instance.{_name(db)}.port\n"
                    "    dbname   = var.db_name\n"
                    "  })"
                ))

        for key in spec.of_kind(Kind.KEY_MANAGEMENT):
            b = f.resource("aws_kms_key", _name(key))
            b.set("description", Raw('"${local.name_prefix} customer managed key"'))
            b.set("deletion_window_in_days", 10)
            b.set("enable_key_rotation", True)
            b.set("tags", _tags())

            alias = f.resource("aws_kms_alias", f"{_name(key)}_alias")
            alias.set("name", Raw('"alias/${local.name_prefix}"'))
            alias.set("target_key_id", ref("aws_kms_key", _name(key), "key_id"))

        return f

    # ------------------------------------------------------------------
    # iam
    # ------------------------------------------------------------------

    def _iam(self, spec: InfrastructureSpec) -> HclFile:
        f = HclFile("IAM roles for the workloads in this stack.")

        for role in spec.of_kind(Kind.IAM_ROLE):
            service = role.properties.get("service", "ec2.amazonaws.com")
            b = f.resource("aws_iam_role", _name(role))
            b.set("name", _prefixed(role.id.replace("_", "-")))
            b.set("assume_role_policy", Raw(
                "jsonencode({\n"
                '    Version = "2012-10-17"\n'
                "    Statement = [{\n"
                '      Action    = "sts:AssumeRole"\n'
                '      Effect    = "Allow"\n'
                f'      Principal = {{ Service = "{service}" }}\n'
                "    }]\n"
                "  })"
            ))
            b.set("tags", _tags())

            if role.id == "lambda_role":
                attach = f.resource("aws_iam_role_policy_attachment", "lambda_basic")
                attach.set("role", ref("aws_iam_role", _name(role), "name"))
                attach.set(
                    "policy_arn",
                    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                )
                if spec.has(Kind.QUEUE):
                    sqs_attach = f.resource("aws_iam_role_policy_attachment", "lambda_sqs")
                    sqs_attach.set("role", ref("aws_iam_role", _name(role), "name"))
                    sqs_attach.set(
                        "policy_arn",
                        "arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole",
                    )
            elif role.id == "instance_role":
                attach = f.resource("aws_iam_role_policy_attachment", "ssm_core")
                attach.set("role", ref("aws_iam_role", _name(role), "name"))
                attach.set("policy_arn", "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")

                profile = f.resource("aws_iam_instance_profile", "instance")
                profile.set("name", _prefixed("instance-profile"))
                profile.set("role", ref("aws_iam_role", _name(role), "name"))
            elif role.id == "task_role":
                attach = f.resource("aws_iam_role_policy_attachment", "ecs_execution")
                attach.set("role", ref("aws_iam_role", _name(role), "name"))
                attach.set(
                    "policy_arn",
                    "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
                )
            elif role.id == "cluster_role":
                attach = f.resource("aws_iam_role_policy_attachment", "eks_cluster")
                attach.set("role", ref("aws_iam_role", _name(role), "name"))
                attach.set("policy_arn", "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy")

        # EKS node groups need their own role; it is never in the IR because it
        # is an implementation detail of the node group, not of the design.
        if spec.has(Kind.KUBERNETES_CLUSTER):
            node = f.resource("aws_iam_role", "eks_node")
            node.set("name", _prefixed("eks-node"))
            node.set("assume_role_policy", Raw(
                "jsonencode({\n"
                '    Version = "2012-10-17"\n'
                "    Statement = [{\n"
                '      Action    = "sts:AssumeRole"\n'
                '      Effect    = "Allow"\n'
                '      Principal = { Service = "ec2.amazonaws.com" }\n'
                "    }]\n"
                "  })"
            ))
            for i, policy in enumerate((
                "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
                "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
                "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
            )):
                attach = f.resource("aws_iam_role_policy_attachment", f"eks_node_{i}")
                attach.set("role", ref("aws_iam_role", "eks_node", "name"))
                attach.set("policy_arn", policy)

        # Inline policies for the data stores the workload was told to use.
        statements: list[str] = []
        if spec.has(Kind.OBJECT_STORAGE):
            bucket = spec.first(Kind.OBJECT_STORAGE)
            statements.append(
                "{\n"
                '      Effect   = "Allow"\n'
                '      Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]\n'
                f"      Resource = [aws_s3_bucket.{_name(bucket)}.arn, "
                f'"${{aws_s3_bucket.{_name(bucket)}.arn}}/*"]\n'
                "    }"
            )
        if spec.has(Kind.NOSQL_TABLE):
            table = spec.first(Kind.NOSQL_TABLE)
            statements.append(
                "{\n"
                '      Effect   = "Allow"\n'
                '      Action   = ["dynamodb:GetItem", "dynamodb:PutItem", '
                '"dynamodb:UpdateItem", "dynamodb:Query", "dynamodb:Scan"]\n'
                f"      Resource = [aws_dynamodb_table.{_name(table)}.arn]\n"
                "    }"
            )
        if spec.has(Kind.SECRET_STORE):
            secret = spec.first(Kind.SECRET_STORE)
            statements.append(
                "{\n"
                '      Effect   = "Allow"\n'
                '      Action   = ["secretsmanager:GetSecretValue"]\n'
                f"      Resource = [aws_secretsmanager_secret.{_name(secret)}.arn]\n"
                "    }"
            )

        roles = [r for r in spec.of_kind(Kind.IAM_ROLE)]
        if statements and roles:
            policy = f.resource("aws_iam_role_policy", "workload_access",
                                "Least-privilege access to the data stores in this design.")
            policy.set("name", _prefixed("workload-access"))
            policy.set("role", ref("aws_iam_role", _name(roles[0]), "id"))
            policy.set("policy", Raw(
                "jsonencode({\n"
                '    Version = "2012-10-17"\n'
                "    Statement = [\n    " + ",\n    ".join(statements) + "\n    ]\n"
                "  })"
            ))

        return f

    # ------------------------------------------------------------------
    # monitoring
    # ------------------------------------------------------------------

    def _monitoring(self, spec: InfrastructureSpec) -> HclFile:
        f = HclFile("CloudWatch alarms.")
        if not spec.has(Kind.MONITORING):
            return f

        asg = spec.first(Kind.AUTOSCALING_GROUP)
        db = spec.first(Kind.SQL_DATABASE)
        instance = spec.first(Kind.VM)
        if asg is None and db is None and instance is None:
            # Nothing to alarm on. Emitting an alert topic here would put a
            # resource in the Terraform that appears nowhere in the shared
            # model, which is exactly the drift this project exists to avoid.
            return f

        topic = spec.first(Kind.TOPIC)
        if topic is None:
            alarm_topic = f.resource(
                "aws_sns_topic", "alerts",
                "Delivery target for the alarms below.",
            )
            alarm_topic.set("name", _prefixed("alerts"))
            alarm_topic.set("tags", _tags())
            topic_ref = ref("aws_sns_topic", "alerts", "arn")
        else:
            topic_ref = ref("aws_sns_topic", _name(topic), "arn")

        if asg is not None:
            a = f.resource("aws_cloudwatch_metric_alarm", "high_cpu")
            a.set("alarm_name", _prefixed("high-cpu"))
            a.set("comparison_operator", "GreaterThanThreshold")
            a.set("evaluation_periods", 2)
            a.set("metric_name", "CPUUtilization")
            a.set("namespace", "AWS/EC2")
            a.set("period", 300)
            a.set("statistic", "Average")
            a.set("threshold", 80)
            a.set("alarm_description", "Average CPU above 80% for 10 minutes")
            a.set("alarm_actions", [topic_ref])
            a.set("dimensions", {
                "AutoScalingGroupName": ref("aws_autoscaling_group", _name(asg), "name")
            })
            a.set("tags", _tags())

        if asg is None and instance is not None:
            a = f.resource("aws_cloudwatch_metric_alarm", "instance_cpu")
            a.set("alarm_name", _prefixed("instance-high-cpu"))
            a.set("comparison_operator", "GreaterThanThreshold")
            a.set("evaluation_periods", 2)
            a.set("metric_name", "CPUUtilization")
            a.set("namespace", "AWS/EC2")
            a.set("period", 300)
            a.set("statistic", "Average")
            a.set("threshold", 80)
            a.set("alarm_description", "Average CPU above 80% for 10 minutes")
            a.set("alarm_actions", [topic_ref])
            index = "[0]" if instance.count > 1 else ""
            a.set("dimensions", {
                "InstanceId": Raw(f"aws_instance.{_name(instance)}{index}.id")
            })
            a.set("tags", _tags())

        if db is not None:
            a = f.resource("aws_cloudwatch_metric_alarm", "db_storage")
            a.set("alarm_name", _prefixed("db-low-storage"))
            a.set("comparison_operator", "LessThanThreshold")
            a.set("evaluation_periods", 1)
            a.set("metric_name", "FreeStorageSpace")
            a.set("namespace", "AWS/RDS")
            a.set("period", 300)
            a.set("statistic", "Average")
            a.set("threshold", 2000000000)
            a.set("alarm_description", "Less than 2 GB of free database storage")
            a.set("alarm_actions", [topic_ref])
            a.set("dimensions", {
                "DBInstanceIdentifier": ref("aws_db_instance", _name(db), "id")
            })
            a.set("tags", _tags())

        return f

    # ------------------------------------------------------------------
    # outputs
    # ------------------------------------------------------------------

    def _outputs(self, spec: InfrastructureSpec) -> str:
        f = HclFile("Useful values after apply.")

        vpc = spec.first(Kind.VPC)
        if vpc is not None:
            f.output("vpc_id").set_all({
                "description": (
                    "ID of the VPC this project deploys into"
                    if vpc.is_external else "ID of the generated VPC"
                ),
                # Must follow the same created-or-looked-up rule as every
                # other reference, or the output points at nothing.
                "value": _vpc_id(spec),
            })

        lb = spec.first(Kind.LOAD_BALANCER)
        if lb is not None:
            f.output("load_balancer_dns").set_all({
                "description": "Public DNS name of the load balancer",
                "value": ref("aws_lb", _name(lb), "dns_name"),
            })

        cdn = spec.first(Kind.CDN)
        if cdn is not None:
            f.output("cdn_domain").set_all({
                "description": "CloudFront distribution domain",
                "value": ref("aws_cloudfront_distribution", _name(cdn), "domain_name"),
            })

        api = spec.first(Kind.API_GATEWAY)
        if api is not None:
            f.output("api_endpoint").set_all({
                "description": "Base URL of the HTTP API",
                "value": ref("aws_apigatewayv2_api", _name(api), "api_endpoint"),
            })

        db = spec.first(Kind.SQL_DATABASE)
        if db is not None:
            f.output("database_endpoint").set_all({
                "description": "Connection endpoint for the managed database",
                "value": ref("aws_db_instance", _name(db), "address"),
                "sensitive": True,
            })

        for cluster in spec.of_kind(Kind.SQL_CLUSTER):
            f.output(f"{_name(cluster)}_endpoint").set_all({
                "description": "Aurora cluster writer endpoint",
                "value": ref("aws_rds_cluster", _name(cluster), "endpoint"),
                "sensitive": True,
            })
            f.output(f"{_name(cluster)}_reader_endpoint").set_all({
                "description": "Aurora cluster reader endpoint",
                "value": ref("aws_rds_cluster", _name(cluster), "reader_endpoint"),
                "sensitive": True,
            })

        for bucket in spec.of_kind(Kind.OBJECT_STORAGE):
            f.output(f"{_name(bucket)}_bucket").set_all({
                "description": f"Name of the {bucket.name}",
                "value": ref("aws_s3_bucket", _name(bucket), "id"),
            })

        eks = spec.first(Kind.KUBERNETES_CLUSTER)
        if eks is not None:
            f.output("eks_cluster_endpoint").set_all({
                "description": "Kubernetes API server endpoint",
                "value": ref("aws_eks_cluster", _name(eks), "endpoint"),
            })

        if not f:
            f.output("project_name").set_all({
                "description": "Name prefix used for this deployment",
                "value": Raw("local.name_prefix"),
            })
        return f.render()
