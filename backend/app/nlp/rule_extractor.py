"""Deterministic, dependency-free requirement extractor.

This is the default NLP backend.  It runs offline, produces identical output
for identical input, and needs no API key -- which makes it both the fallback
when no LLM is configured and the oracle the test-suite asserts against.

The pipeline is a small, classic IE stack:

    normalise -> phrase match (longest-first, spans consumed)
              -> attribute extraction (counts, sizes, engines, region)
              -> qualifier detection (HA, public/private, environment)

Everything it emits is tagged with the span of text that produced it, so the
UI can show the user *why* a resource ended up in their architecture.
"""

from __future__ import annotations

import re

from app.models.ir import InfrastructureSpec, Kind, Origin, Resource, slugify
from app.nlp.base import Extractor
from app.nlp.catalog import (
    AUTOSCALING_TRIGGERS,
    DB_ENGINES,
    DEFAULT_OS,
    ENVIRONMENT_CANONICAL,
    ENVIRONMENTS,
    LEXICON,
    LEXICON_TOKENS,
    LOAD_BALANCER_TRIGGERS,
    OPERATING_SYSTEMS,
    PRIVATE_PLACEMENT_MARKERS,
    PROTOCOL_PORTS,
    REGION_ALIASES,
    service_for,
)

NUMBER_WORDS: dict[str, int] = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
    "a couple of": 2, "a pair of": 2, "several": 3, "a few": 3,
}

# "3 ", "three ", "a couple of " immediately before a matched phrase.
_QUANTITY_RE = re.compile(
    r"(?:(\d{1,3})|\b(a couple of|a pair of|a few|several|"
    r"one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b)\s+"
    r"(?:[a-z0-9.\-]+\s+){0,2}$"
)

_INSTANCE_TYPE_RE = re.compile(
    r"\b((?:t2|t3|t3a|t4g|m5|m6i|m6g|c5|c6i|c6g|r5|r6i|r6g|i3|g4dn)\."
    r"(?:nano|micro|small|medium|large|xlarge|2xlarge|4xlarge|8xlarge|12xlarge))\b"
)

_STORAGE_RE = re.compile(r"\b(\d{1,5})\s*(gb|gib|tb|tib)\b")
_PORT_RE = re.compile(r"\bport\s+(\d{2,5})\b")
_AZ_RE = re.compile(r"\b(\d)\s*(?:availability zones?|azs?)\b")
_NODE_COUNT_RE = re.compile(
    r"\b(\d{1,2})\s+(?:[a-z0-9.\-]+\s+){0,2}(?:nodes?|workers?|replicas?|tasks?|pods?)\b"
)

HA_MARKERS = (
    "high availability", "highly available", "high-availability", "multi-az",
    "multi az", "fault tolerant", "fault-tolerant", "redundant", "resilient",
    "no single point of failure", "failover", "production grade", "production-grade",
)

PUBLIC_MARKERS = ("internet facing", "internet-facing", "public facing", "publicly accessible",
                  "public", "open to the internet")

ENCRYPTION_MARKERS = ("encrypted", "encryption", "at rest", "kms")

BACKUP_MARKERS = ("backup", "backups", "point in time recovery", "point-in-time",
                  "snapshot", "snapshots", "retention")


def normalise(text: str) -> str:
    """Lower-case and collapse whitespace/punctuation for reliable matching."""
    text = text.lower().replace("’", "'")
    text = re.sub(r"[,;:()\[\]{}\"]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class Span:
    __slots__ = ("start", "end")

    def __init__(self, start: int, end: int) -> None:
        self.start = start
        self.end = end

    def overlaps(self, start: int, end: int) -> bool:
        return start < self.end and end > self.start


class RuleExtractor(Extractor):
    """Longest-match phrase extraction over the service lexicon."""

    name = "rule"

    def extract(self, prompt: str) -> InfrastructureSpec:
        raw = prompt.strip()
        text = normalise(raw)
        spec = InfrastructureSpec(prompt=raw, extractor=self.name)

        if not text:
            spec.warn("Empty requirement: nothing to extract.")
            return spec

        spec.region = self._region(text, spec)
        spec.environment = self._environment(text)
        spec.high_availability = self._high_availability(text)
        spec.private_placement_requested = any(
            marker in text for marker in PRIVATE_PLACEMENT_MARKERS
        )
        spec.availability_zones = self._availability_zones(text, spec.high_availability)
        spec.name = self._project_name(raw, spec.environment)

        consumed: list[Span] = []
        seen_kinds: dict[Kind, Resource] = {}

        for entry in LEXICON:
            # Longest phrase first so "application load balancer" wins over "alb".
            for phrase in sorted(entry.phrases, key=len, reverse=True):
                # Trailing (e)s makes plurals match without doubling the lexicon.
                pattern = re.compile(
                    r"(?<![a-z0-9])" + re.escape(phrase) + r"(?:e?s)?(?![a-z0-9])"
                )
                for match in pattern.finditer(text):
                    start, end = match.span()
                    if any(s.overlaps(start, end) for s in consumed):
                        continue
                    consumed.append(Span(start, end))

                    count = self._quantity(text, start, consumed)
                    evidence = self._evidence(text, start, end)

                    if entry.kind in seen_kinds:
                        # Same service mentioned twice: keep the larger count
                        # rather than creating a duplicate resource.
                        existing = seen_kinds[entry.kind]
                        if count > existing.count:
                            existing.count = count
                        continue

                    info = service_for(entry.kind)
                    resource = Resource(
                        id=slugify(entry.default_name),
                        kind=entry.kind,
                        name=info.display,
                        tier=info.tier,
                        origin=Origin.EXPLICIT,
                        count=count,
                        # Longer, more specific phrases are stronger evidence
                        # than a bare acronym.
                        confidence=round(min(0.99, 0.80 + 0.05 * len(phrase.split())), 2),
                        evidence=evidence,
                        reason=f"The requirement names {phrase!r}.",
                    )
                    spec.add(resource)
                    seen_kinds[entry.kind] = resource

        self._merge_compute(text, spec, seen_kinds)
        self._apply_triggers(text, spec, seen_kinds)
        self._attach_properties(text, spec)
        self._flag_ambiguity(text, spec, seen_kinds)
        spec.summary = self._summary(spec)
        return spec

    # -- trigger phrases ---------------------------------------------------

    def _apply_triggers(
        self, text: str, spec: InfrastructureSpec, seen: dict[Kind, Resource]
    ) -> None:
        """Add the two services the resource rules allow to be inferred.

        A load balancer and a scaling group may appear without being named,
        but only for the specific phrases below -- "high availability",
        "auto scaling", "web tier", or more than one instance. Everything else
        in the design must be named outright. Each addition records the phrase
        that caused it, so an unwanted one is traceable to the words that
        produced it rather than to a hidden heuristic.
        """
        vm = seen.get(Kind.VM)
        multiple_instances = vm is not None and vm.count > 1

        def matched(triggers: tuple[str, ...]) -> str | None:
            for phrase in triggers:
                if phrase in text:
                    return phrase
            return None

        # ECS and EKS scale through their own schedulers, so "highly available"
        # must not also bolt an EC2 scaling group onto a container platform.
        managed_platform = (
            Kind.CONTAINER_SERVICE in seen or Kind.KUBERNETES_CLUSTER in seen
        )

        # -- auto scaling group ------------------------------------------
        if Kind.AUTOSCALING_GROUP not in seen and not managed_platform:
            phrase = matched(AUTOSCALING_TRIGGERS)
            if phrase:
                self._add_triggered(
                    spec, seen, Kind.AUTOSCALING_GROUP, "app_asg",
                    f"The requirement asks for {phrase!r}, which needs a scaling group.",
                    phrase,
                )

        # -- load balancer -----------------------------------------------
        if Kind.LOAD_BALANCER not in seen:
            phrase = matched(LOAD_BALANCER_TRIGGERS)
            if phrase:
                self._add_triggered(
                    spec, seen, Kind.LOAD_BALANCER, "alb",
                    f"The requirement asks for {phrase!r}, which needs traffic "
                    "spread across instances.",
                    phrase,
                )
            elif multiple_instances:
                self._add_triggered(
                    spec, seen, Kind.LOAD_BALANCER, "alb",
                    f"{vm.count} instances were requested, so traffic has to be "
                    "distributed between them.",
                    f"{vm.count} instances",
                )

    @staticmethod
    def _add_triggered(
        spec: InfrastructureSpec,
        seen: dict[Kind, Resource],
        kind: Kind,
        resource_id: str,
        reason: str,
        evidence: str,
    ) -> None:
        info = service_for(kind)
        resource = Resource(
            id=resource_id,
            kind=kind,
            name=info.display,
            tier=info.tier,
            origin=Origin.EXPLICIT,
            confidence=0.75,  # inferred from intent, not named outright
            evidence=evidence,
            reason=reason,
        )
        spec.add(resource)
        seen[kind] = resource
        spec.note(f"{resource.name}: {reason}")

    # -- attribute helpers -------------------------------------------------

    @staticmethod
    def _quantity(text: str, phrase_start: int, consumed: list[Span]) -> int:
        """Count stated just before a service phrase, e.g. "three web servers".

        Digits that belong to a service name already matched elsewhere are
        ignored -- otherwise the 53 in "Route 53 custom domain" would be read
        as a quantity for the phrase that follows it.
        """
        left = max(0, phrase_start - 40)
        window = text[left:phrase_start]
        match = _QUANTITY_RE.search(window)
        if not match:
            return 1
        if match.group(1):
            start = left + match.start(1)
            end = left + match.end(1)
            if any(s.overlaps(start, end) for s in consumed):
                return 1
            preceding = text[:start].strip().split()
            if preceding and preceding[-1] in LEXICON_TOKENS:
                return 1  # part of a product name, e.g. "route 53"
            return max(1, min(100, int(match.group(1))))
        return NUMBER_WORDS.get(match.group(2), 1)

    @staticmethod
    def _evidence(text: str, start: int, end: int) -> str:
        left = max(0, start - 30)
        right = min(len(text), end + 30)
        snippet = text[left:right].strip()
        prefix = "..." if left > 0 else ""
        suffix = "..." if right < len(text) else ""
        return f"{prefix}{snippet}{suffix}"

    @staticmethod
    def _region(text: str, spec: InfrastructureSpec) -> str:
        for alias, region in sorted(REGION_ALIASES.items(), key=lambda kv: -len(kv[0])):
            if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text):
                return region
        spec.note("No region stated; defaulting to us-east-1.")
        return "us-east-1"

    @staticmethod
    def _environment(text: str) -> str:
        for env in ENVIRONMENTS:
            if re.search(r"\b" + re.escape(env) + r"\b", text):
                return ENVIRONMENT_CANONICAL.get(env, "dev")
        return "dev"

    @staticmethod
    def _high_availability(text: str) -> bool:
        return any(marker in text for marker in HA_MARKERS)

    @staticmethod
    def _availability_zones(text: str, ha: bool) -> int:
        match = _AZ_RE.search(text)
        if match:
            return max(1, min(6, int(match.group(1))))
        return 3 if ha else 2

    @staticmethod
    def _project_name(raw: str, environment: str) -> str:
        """Derive a project slug from the first few meaningful words."""
        stop = {
            "a", "an", "the", "i", "we", "need", "want", "build", "create", "deploy",
            "set", "up", "setup", "please", "make", "me", "for", "with", "on", "aws",
            "using", "generate", "provision", "design",
        }
        words = [
            w for w in re.findall(r"[a-z0-9]+", raw.lower())
            if w not in stop and not w.isdigit()
        ]
        base = "-".join(words[:3]) if words else "generated"
        return f"{base}-{environment}"[:48].strip("-")

    def _attach_properties(self, text: str, spec: InfrastructureSpec) -> None:
        """Pull sizes, engines and flags out of the text onto the right resources."""
        instance_type = _INSTANCE_TYPE_RE.search(text)
        storage = _STORAGE_RE.search(text)
        port = _PORT_RE.search(text)
        node_match = _NODE_COUNT_RE.search(text)
        nodes = int(node_match.group(1)) if node_match else 0
        encrypted = any(m in text for m in ENCRYPTION_MARKERS)
        backups = any(m in text for m in BACKUP_MARKERS)
        public = any(m in text for m in PUBLIC_MARKERS)
        operating_system = self._operating_system(text)
        stated_ports = self._stated_ports(text)

        for resource in spec.resources:
            props = resource.properties

            if resource.kind is Kind.SECURITY_GROUP and stated_ports:
                props["ingress_ports"] = stated_ports
                props["ports_from_prompt"] = True

            if resource.kind in (Kind.VM, Kind.AUTOSCALING_GROUP, Kind.BASTION):
                name, ami_filter, owner, ssh_user = operating_system
                props["os"] = name
                props["ami_name_filter"] = ami_filter
                props["ami_owner"] = owner
                props["ssh_user"] = ssh_user

            if resource.kind in (Kind.VM, Kind.AUTOSCALING_GROUP, Kind.BASTION):
                props["instance_type"] = (
                    instance_type.group(1) if instance_type
                    else ("t3.medium" if spec.environment == "prod" else "t3.micro")
                )
                if resource.kind == Kind.AUTOSCALING_GROUP:
                    props["min_size"] = max(2 if spec.high_availability else 1, resource.count)
                    props["max_size"] = max(props["min_size"] * 3, 4)
                    props["desired_capacity"] = props["min_size"]

            if resource.kind == Kind.SQL_DATABASE:
                engine, version = self._db_engine(text)
                props["engine"] = engine
                props["engine_version"] = version
                props["instance_class"] = (
                    "db.t3.medium" if spec.environment == "prod" else "db.t3.micro"
                )
                props["allocated_storage"] = self._storage_gb(storage, default=20)
                props["multi_az"] = spec.high_availability
                props["backup_retention_period"] = 7 if backups or spec.environment == "prod" else 1
                props["storage_encrypted"] = True

            if resource.kind == Kind.OBJECT_STORAGE:
                props["versioning"] = spec.environment == "prod" or backups
                props["encrypted"] = True
                props["public_read"] = "static website" in text or "static site" in text

            if resource.kind == Kind.LOAD_BALANCER:
                props["internal"] = not public and "internal" in text
                props["listener_port"] = int(port.group(1)) if port else 80
                props["scheme"] = "internal" if props["internal"] else "internet-facing"

            if resource.kind == Kind.CACHE:
                props["node_type"] = "cache.t3.micro"
                props["num_nodes"] = 2 if spec.high_availability else 1

            if resource.kind == Kind.FUNCTION:
                props["runtime"] = self._runtime(text)
                props["memory_size"] = 512
                props["timeout"] = 30

            if resource.kind == Kind.KUBERNETES_CLUSTER:
                props["node_count"] = nodes or max(2, resource.count)
                props["node_instance_type"] = (
                    instance_type.group(1) if instance_type else "t3.medium"
                )
                props["version"] = "1.29"

            if resource.kind == Kind.CONTAINER_SERVICE:
                props["cpu"] = 512
                props["memory"] = 1024
                props["desired_count"] = nodes or max(
                    2 if spec.high_availability else 1, resource.count
                )

            if resource.kind == Kind.NOSQL_TABLE:
                props["billing_mode"] = "PAY_PER_REQUEST"
                props["hash_key"] = "id"
                props["point_in_time_recovery"] = backups or spec.environment == "prod"

            if encrypted:
                props.setdefault("encryption_requested", True)

    @staticmethod
    def _operating_system(text: str) -> tuple[str, str, str, str]:
        """Match the requested OS to an AMI lookup, defaulting to Amazon Linux."""
        for name, ami_filter, owner, ssh_user in OPERATING_SYSTEMS:
            if name in text:
                return name, ami_filter, owner, ssh_user
        return DEFAULT_OS

    @staticmethod
    def _stated_ports(text: str) -> list[int]:
        """Ports the user actually named, by number or by protocol.

        "allowing SSH and HTTP" has to produce 22 and 80 rather than a
        plausible default, because a firewall rule the user did not ask for is
        the kind of guess that gets noticed in production.
        """
        ports: list[int] = []
        for match in re.finditer(r"\bports?\s+((?:\d{1,5})(?:\s*(?:,|and|&)\s*\d{1,5})*)",
                                 text):
            for number in re.findall(r"\d{1,5}", match.group(1)):
                value = int(number)
                if 1 <= value <= 65535:
                    ports.append(value)

        for word, port in PROTOCOL_PORTS:
            if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text):
                ports.append(port)

        # Preserve first-mention order, drop duplicates.
        return list(dict.fromkeys(ports))

    @staticmethod
    def _db_engine(text: str) -> tuple[str, str]:
        for phrase, engine, version in DB_ENGINES:
            if phrase in text:
                return engine, version
        return "postgres", "15.5"

    @staticmethod
    def _storage_gb(match: re.Match[str] | None, default: int) -> int:
        if not match:
            return default
        value = int(match.group(1))
        if match.group(2) in ("tb", "tib"):
            value *= 1024
        return max(20, min(16384, value))

    @staticmethod
    def _runtime(text: str) -> str:
        for phrase, runtime in (
            ("python", "python3.12"), ("node", "nodejs20.x"), ("javascript", "nodejs20.x"),
            ("typescript", "nodejs20.x"), ("java", "java21"), ("go", "provided.al2023"),
            ("dotnet", "dotnet8"), (".net", "dotnet8"), ("ruby", "ruby3.3"),
        ):
            if phrase in text:
                return runtime
        return "python3.12"

    @staticmethod
    def _merge_compute(
        text: str, spec: InfrastructureSpec, seen: dict[Kind, Resource]
    ) -> None:
        """Fold a bare instance mention into the scaling group that owns it.

        "an auto scaling group of EC2 instances" matches both phrases, but it
        describes one compute pool, not two. When the two mentions sit close
        together in the text, the instance mention is treated as the group's
        member description rather than a separate standalone server.
        """
        asg = seen.get(Kind.AUTOSCALING_GROUP)
        vm = seen.get(Kind.VM)
        if asg is None or vm is None:
            return

        joiners = ("auto scaling group of", "autoscaling group of", "asg of",
                   "auto scaling group with", "scaling group of")
        near = any(j in text for j in joiners)
        if not near:
            # Fall back to proximity: within ~50 characters of each other.
            positions = []
            for resource in (asg, vm):
                if resource.evidence:
                    positions.append(text.find(resource.evidence.strip(".")))
            near = len(positions) == 2 and -1 not in positions and abs(
                positions[0] - positions[1]
            ) < 50

        if not near:
            return

        if vm.count > 1 and asg.count == 1:
            asg.count = vm.count
        spec.resources.remove(vm)
        del seen[Kind.VM]
        spec.note(
            "The EC2 instances described are the auto scaling group's members, "
            "so they are generated as a launch template rather than standalone hosts."
        )

    @staticmethod
    def _flag_ambiguity(
        text: str, spec: InfrastructureSpec, seen: dict[Kind, Resource]
    ) -> None:
        if not spec.resources:
            spec.warn(
                "No cloud resources were recognised in the description. "
                "Try naming concrete services, e.g. 'two EC2 web servers behind "
                "a load balancer with a PostgreSQL database'."
            )
        if Kind.SQL_DATABASE in seen and "database" in text and not any(
            phrase in text for phrase, _, _ in DB_ENGINES
        ):
            spec.note("Database engine not specified; assuming PostgreSQL 15.5.")
        if Kind.VM in seen and Kind.AUTOSCALING_GROUP in seen:
            spec.warn(
                "Both standalone instances and an auto scaling group were requested; "
                "they are generated as separate compute pools."
            )

    @staticmethod
    def _summary(spec: InfrastructureSpec) -> str:
        if not spec.resources:
            return "No recognisable infrastructure requirements."
        parts = []
        for r in spec.resources:
            label = service_for(r.kind).display
            parts.append(f"{r.count}x {label}" if r.count > 1 else label)
        tail = ", ".join(parts)
        ha = " with multi-AZ high availability" if spec.high_availability else ""
        return f"{spec.environment} environment in {spec.region}{ha}: {tail}."
