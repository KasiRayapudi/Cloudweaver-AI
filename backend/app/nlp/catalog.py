"""Service catalog: canonical kinds <-> cloud services and vocabulary.

Two things live here and nowhere else:

1. ``AWS_CATALOG`` -- what each canonical ``Kind`` becomes on a given cloud
   provider (display name, Terraform resource type, diagram styling).
2. ``LEXICON`` -- the phrases the rule based extractor matches against.

Keeping both tables in one module means adding support for a new service is a
single edit rather than a hunt through the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.ir import Kind, Provider, Tier


@dataclass(frozen=True)
class ServiceInfo:
    kind: Kind
    display: str                 # label drawn in the diagram
    terraform_type: str          # primary aws_* resource emitted
    tier: Tier
    category: str                # drives diagram colour
    glyph: str = ""              # short badge text drawn inside the node


# --------------------------------------------------------------------------
# AWS catalog
# --------------------------------------------------------------------------
AWS_CATALOG: dict[Kind, ServiceInfo] = {
    Kind.VPC: ServiceInfo(Kind.VPC, "VPC", "aws_vpc", Tier.NETWORK, "network", "VPC"),
    Kind.SUBNET_PUBLIC: ServiceInfo(
        Kind.SUBNET_PUBLIC, "Public Subnet", "aws_subnet", Tier.NETWORK, "network", "SUB"
    ),
    Kind.SUBNET_PRIVATE: ServiceInfo(
        Kind.SUBNET_PRIVATE, "Private Subnet", "aws_subnet", Tier.NETWORK, "network", "SUB"
    ),
    Kind.INTERNET_GATEWAY: ServiceInfo(
        Kind.INTERNET_GATEWAY, "Internet Gateway", "aws_internet_gateway",
        Tier.NETWORK, "network", "IGW",
    ),
    Kind.NAT_GATEWAY: ServiceInfo(
        Kind.NAT_GATEWAY, "NAT Gateway", "aws_nat_gateway", Tier.PUBLIC, "network", "NAT"
    ),
    Kind.ROUTE_TABLE: ServiceInfo(
        Kind.ROUTE_TABLE, "Route Table", "aws_route_table", Tier.NETWORK, "network", "RT"
    ),
    Kind.SECURITY_GROUP: ServiceInfo(
        Kind.SECURITY_GROUP, "Security Group", "aws_security_group",
        Tier.SECURITY, "security", "SG",
    ),
    Kind.ELASTIC_IP: ServiceInfo(
        Kind.ELASTIC_IP, "Elastic IP", "aws_eip", Tier.PUBLIC, "network", "EIP"
    ),
    Kind.VM: ServiceInfo(Kind.VM, "EC2 Instance", "aws_instance", Tier.APP, "compute", "EC2"),
    Kind.AUTOSCALING_GROUP: ServiceInfo(
        Kind.AUTOSCALING_GROUP, "Auto Scaling Group", "aws_autoscaling_group",
        Tier.APP, "compute", "ASG",
    ),
    Kind.CONTAINER_SERVICE: ServiceInfo(
        Kind.CONTAINER_SERVICE, "ECS Fargate Service", "aws_ecs_service",
        Tier.APP, "compute", "ECS",
    ),
    Kind.KUBERNETES_CLUSTER: ServiceInfo(
        Kind.KUBERNETES_CLUSTER, "EKS Cluster", "aws_eks_cluster", Tier.APP, "compute", "EKS"
    ),
    Kind.FUNCTION: ServiceInfo(
        Kind.FUNCTION, "Lambda Function", "aws_lambda_function", Tier.APP, "compute", "FN"
    ),
    Kind.CONTAINER_REGISTRY: ServiceInfo(
        Kind.CONTAINER_REGISTRY, "ECR Repository", "aws_ecr_repository",
        Tier.APP, "compute", "ECR",
    ),
    Kind.BASTION: ServiceInfo(
        Kind.BASTION, "Bastion Host", "aws_instance", Tier.PUBLIC, "compute", "SSH"
    ),
    Kind.LOAD_BALANCER: ServiceInfo(
        Kind.LOAD_BALANCER, "Application Load Balancer", "aws_lb",
        Tier.PUBLIC, "traffic", "ALB",
    ),
    Kind.NETWORK_LOAD_BALANCER: ServiceInfo(
        Kind.NETWORK_LOAD_BALANCER, "Network Load Balancer", "aws_lb",
        Tier.PUBLIC, "traffic", "NLB",
    ),
    Kind.GATEWAY_LOAD_BALANCER: ServiceInfo(
        Kind.GATEWAY_LOAD_BALANCER, "Gateway Load Balancer", "aws_lb",
        Tier.PUBLIC, "traffic", "GWLB",
    ),
    Kind.CERTIFICATE: ServiceInfo(
        Kind.CERTIFICATE, "ACM Certificate", "aws_acm_certificate",
        Tier.EDGE, "security", "TLS",
    ),
    Kind.TARGET_GROUP: ServiceInfo(
        Kind.TARGET_GROUP, "Target Group", "aws_lb_target_group", Tier.PUBLIC, "traffic", "TG"
    ),
    Kind.API_GATEWAY: ServiceInfo(
        Kind.API_GATEWAY, "API Gateway", "aws_apigatewayv2_api", Tier.EDGE, "traffic", "API"
    ),
    Kind.CDN: ServiceInfo(
        Kind.CDN, "CloudFront", "aws_cloudfront_distribution", Tier.EDGE, "traffic", "CDN"
    ),
    Kind.DNS_ZONE: ServiceInfo(
        Kind.DNS_ZONE, "Route 53 Zone", "aws_route53_zone", Tier.GLOBAL, "traffic", "DNS"
    ),
    Kind.WAF: ServiceInfo(
        Kind.WAF, "WAF Web ACL", "aws_wafv2_web_acl", Tier.EDGE, "security", "WAF"
    ),
    Kind.SQL_DATABASE: ServiceInfo(
        Kind.SQL_DATABASE, "RDS Instance", "aws_db_instance", Tier.DATA, "data", "RDS"
    ),
    Kind.SQL_CLUSTER: ServiceInfo(
        Kind.SQL_CLUSTER, "Aurora Cluster", "aws_rds_cluster", Tier.DATA, "data", "AUR"
    ),
    Kind.NOSQL_TABLE: ServiceInfo(
        Kind.NOSQL_TABLE, "DynamoDB Table", "aws_dynamodb_table", Tier.DATA, "data", "DDB"
    ),
    Kind.CACHE: ServiceInfo(
        Kind.CACHE, "ElastiCache Redis", "aws_elasticache_replication_group",
        Tier.DATA, "data", "CCH",
    ),
    Kind.OBJECT_STORAGE: ServiceInfo(
        Kind.OBJECT_STORAGE, "S3 Bucket", "aws_s3_bucket", Tier.DATA, "storage", "S3"
    ),
    Kind.FILE_STORAGE: ServiceInfo(
        Kind.FILE_STORAGE, "EFS File System", "aws_efs_file_system", Tier.DATA, "storage", "EFS"
    ),
    Kind.DATA_WAREHOUSE: ServiceInfo(
        Kind.DATA_WAREHOUSE, "Redshift Cluster", "aws_redshift_cluster",
        Tier.DATA, "data", "RSH",
    ),
    Kind.QUEUE: ServiceInfo(
        Kind.QUEUE, "SQS Queue", "aws_sqs_queue", Tier.APP, "integration", "SQS"
    ),
    Kind.TOPIC: ServiceInfo(
        Kind.TOPIC, "SNS Topic", "aws_sns_topic", Tier.APP, "integration", "SNS"
    ),
    Kind.EVENT_BUS: ServiceInfo(
        Kind.EVENT_BUS, "EventBridge Bus", "aws_cloudwatch_event_bus",
        Tier.APP, "integration", "EVT",
    ),
    Kind.IAM_ROLE: ServiceInfo(
        Kind.IAM_ROLE, "IAM Role", "aws_iam_role", Tier.SECURITY, "security", "IAM"
    ),
    Kind.SECRET_STORE: ServiceInfo(
        Kind.SECRET_STORE, "Secrets Manager", "aws_secretsmanager_secret",
        Tier.SECURITY, "security", "SEC",
    ),
    Kind.KEY_MANAGEMENT: ServiceInfo(
        Kind.KEY_MANAGEMENT, "KMS Key", "aws_kms_key", Tier.SECURITY, "security", "KMS"
    ),
    Kind.MONITORING: ServiceInfo(
        Kind.MONITORING, "CloudWatch", "aws_cloudwatch_metric_alarm", Tier.OPS, "ops", "CW"
    ),
}

CATALOGS: dict[Provider, dict[Kind, ServiceInfo]] = {Provider.AWS: AWS_CATALOG}


def service_for(kind: Kind, provider: Provider = Provider.AWS) -> ServiceInfo:
    catalog = CATALOGS.get(provider, AWS_CATALOG)
    info = catalog.get(kind)
    if info is None:  # pragma: no cover - guarded by the Kind enum
        raise KeyError(f"no catalog entry for kind {kind!r} on provider {provider!r}")
    return info


# --------------------------------------------------------------------------
# Vocabulary used by the rule based extractor
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class LexEntry:
    kind: Kind
    phrases: tuple[str, ...]
    default_name: str
    weight: float = 1.0
    properties: dict = field(default_factory=dict)


# Order matters. Entries are scanned top to bottom and matched spans are
# consumed, so more specific services must appear before generic ones --
# otherwise "nat gateway" would be eaten by "gateway", and "redis cluster"
# by "cluster".
LEXICON: tuple[LexEntry, ...] = (
    LexEntry(Kind.KUBERNETES_CLUSTER,
             ("kubernetes cluster", "kubernetes", "k8s", "eks cluster", "eks",
              "container orchestration"),
             "eks_cluster"),
    LexEntry(Kind.CONTAINER_SERVICE,
             ("ecs fargate", "fargate", "ecs service", "ecs", "container service",
              "containerised service", "containerized service", "docker container",
              "docker service", "containers"),
             "ecs_service"),
    LexEntry(Kind.CONTAINER_REGISTRY,
             ("container registry", "ecr", "docker registry", "image registry"),
             "ecr_repo"),
    LexEntry(Kind.FUNCTION,
             ("lambda function", "aws lambda", "lambda", "serverless function",
              "cloud function", "serverless"),
             "lambda_fn"),
    LexEntry(Kind.API_GATEWAY,
             ("api gateway", "rest api endpoint", "http api", "apigateway"),
             "api_gateway"),
    LexEntry(Kind.CDN,
             ("cloudfront", "cdn", "content delivery network", "edge cache"),
             "cdn"),
    LexEntry(Kind.WAF,
             ("web application firewall", "waf", "web acl"),
             "waf"),
    LexEntry(Kind.DNS_ZONE,
             ("route 53", "route53", "dns zone", "hosted zone", "custom domain", "dns"),
             "dns_zone"),
    LexEntry(Kind.NAT_GATEWAY,
             ("nat gateway", "nat instance", "nat"),
             "nat_gateway"),
    LexEntry(Kind.INTERNET_GATEWAY,
             ("internet gateway", "igw"),
             "internet_gateway"),
    LexEntry(Kind.NETWORK_LOAD_BALANCER,
             ("network load balancer", "layer 4 load balancer", "l4 load balancer",
              "tcp load balancer", "nlb"),
             "nlb"),
    LexEntry(Kind.GATEWAY_LOAD_BALANCER,
             ("gateway load balancer", "gwlb", "appliance load balancer"),
             "gwlb"),
    LexEntry(Kind.LOAD_BALANCER,
             ("application load balancer", "layer 7 load balancer", "l7 load balancer",
              "http load balancer", "load balancer", "load-balancer", "elb", "alb",
              "load balancing", "load balanced"),
             "alb"),
    LexEntry(Kind.CERTIFICATE,
             ("acm certificate", "tls certificate", "ssl certificate",
              "certificate manager", "acm"),
             "certificate"),
    LexEntry(Kind.AUTOSCALING_GROUP,
             ("auto scaling group", "autoscaling group", "auto-scaling", "autoscaling",
              "auto scaling", "scale automatically", "scales automatically",
              "scale out", "elastic scaling"),
             "app_asg"),
    LexEntry(Kind.BASTION,
             ("bastion host", "bastion", "jump box", "jump host"),
             "bastion"),
    LexEntry(Kind.VM,
             ("ec2 instance", "ec2", "virtual machine", "web server", "app server",
              "application server", "compute instance", "backend server", "server", "vm"),
             "app_server"),
    LexEntry(Kind.SQL_CLUSTER,
             ("aurora postgresql", "aurora postgres", "aurora mysql",
              "aurora serverless", "aurora cluster", "aurora"),
             "app_db_cluster"),
    LexEntry(Kind.SQL_DATABASE,
             ("postgresql database", "postgres database",
              "mysql database", "rds", "postgresql", "postgres", "mysql",
              "mariadb", "sql server", "relational database", "sql database", "database", "db"),
             "app_db"),
    LexEntry(Kind.NOSQL_TABLE,
             ("dynamodb table", "dynamodb", "nosql table", "nosql database", "nosql",
              "document database", "mongodb"),
             "app_table"),
    LexEntry(Kind.CACHE,
             ("elasticache", "redis cluster", "redis", "memcached", "cache layer",
              "caching layer", "cache"),
             "app_cache"),
    LexEntry(Kind.DATA_WAREHOUSE,
             ("data warehouse", "redshift", "analytics warehouse"),
             "warehouse"),
    LexEntry(Kind.OBJECT_STORAGE,
             ("s3 bucket", "object storage", "blob storage", "static assets",
              "static website", "static site", "s3", "bucket"),
             "assets"),
    LexEntry(Kind.FILE_STORAGE,
             ("elastic file system", "shared file system", "nfs share", "efs"),
             "shared_fs"),
    LexEntry(Kind.QUEUE,
             ("sqs queue", "message queue", "job queue", "sqs", "queue"),
             "work_queue"),
    LexEntry(Kind.TOPIC,
             ("sns topic", "notification topic", "pub sub", "sns"),
             "notifications"),
    LexEntry(Kind.EVENT_BUS,
             ("eventbridge", "event bus", "event-driven bus"),
             "event_bus"),
    LexEntry(Kind.SECRET_STORE,
             ("secrets manager", "secret store", "parameter store", "secrets"),
             "app_secrets"),
    LexEntry(Kind.KEY_MANAGEMENT,
             ("kms key", "kms", "encryption key", "customer managed key"),
             "kms_key"),
    LexEntry(Kind.MONITORING,
             ("cloudwatch", "monitoring", "observability", "alarms", "logging", "metrics"),
             "monitoring"),
    LexEntry(Kind.VPC,
             ("vpc", "virtual private cloud", "virtual network", "private network"),
             "main"),
    LexEntry(Kind.SUBNET_PUBLIC,
             ("public subnets", "public subnet"),
             "public"),
    LexEntry(Kind.SUBNET_PRIVATE,
             ("private subnets", "private subnet"),
             "private"),
    LexEntry(Kind.SECURITY_GROUP,
             ("security group", "firewall rule", "firewall"),
             "app_sg"),
    LexEntry(Kind.ELASTIC_IP,
             ("elastic ip", "eip", "static public ip", "static ip"),
             "eip"),
    LexEntry(Kind.IAM_ROLE,
             ("iam role", "instance profile", "execution role", "service role", "iam"),
             "instance_role"),
)

# Operating system -> (AMI name filter, owner, ssh user). Ordered most
# specific first so "ubuntu 22.04" beats "ubuntu".
OPERATING_SYSTEMS: tuple[tuple[str, str, str, str], ...] = (
    ("ubuntu 24.04", "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*",
     "099720109477", "ubuntu"),
    ("ubuntu 22.04", "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*",
     "099720109477", "ubuntu"),
    ("ubuntu 20.04", "ubuntu/images/hvm-ssd/ubuntu-focal-20.04-amd64-server-*",
     "099720109477", "ubuntu"),
    ("ubuntu", "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*",
     "099720109477", "ubuntu"),
    ("debian", "debian-12-amd64-*", "136693071363", "admin"),
    ("red hat", "RHEL-9*_HVM-*-x86_64-*", "309956199498", "ec2-user"),
    ("rhel", "RHEL-9*_HVM-*-x86_64-*", "309956199498", "ec2-user"),
    ("windows", "Windows_Server-2022-English-Full-Base-*", "801119661308", "Administrator"),
    ("amazon linux", "al2023-ami-*-x86_64", "amazon", "ec2-user"),
)

DEFAULT_OS: tuple[str, str, str, str] = (
    "amazon linux", "al2023-ami-*-x86_64", "amazon", "ec2-user"
)

# Protocol words -> port. Used when a prompt names protocols rather than
# numbers ("a security group allowing SSH and HTTP").
PROTOCOL_PORTS: tuple[tuple[str, int], ...] = (
    ("ssh", 22),
    ("http", 80),
    ("https", 443),
    ("rdp", 3389),
    ("mysql", 3306),
    ("postgres", 5432),
    ("postgresql", 5432),
    ("redis", 6379),
    ("nfs", 2049),
    ("smtp", 25),
    ("dns", 53),
)

# Phrases that, per the resource rules, justify a load balancer or scaling
# group even when the service itself is not named.
LOAD_BALANCER_TRIGGERS: tuple[str, ...] = (
    "high availability", "highly available", "high-availability",
    "auto scaling", "autoscaling", "auto-scaling", "web tier",
)

AUTOSCALING_TRIGGERS: tuple[str, ...] = (
    "auto scaling", "autoscaling", "auto-scaling", "scale automatically",
    "scales automatically", "scale out", "elastic scaling",
    "high availability", "highly available", "high-availability",
)

# Words that turn a service mention into a refusal. Ordered longest first so
# "do not need" is reported rather than the bare "not" inside it.
NEGATION_CUES: tuple[str, ...] = (
    "no need for", "do not need", "dont need", "don't need", "not required",
    "rather than", "instead of", "as well as not",
    "without", "excluding", "exclude", "except", "omit", "omitting",
    "avoid", "skip", "no", "not",
)

# Words that defer a service to some later time. "maybe add a database later"
# is not a request for a database today.
HEDGE_CUES: tuple[str, ...] = (
    "in the future", "in future", "down the line", "at some point",
    "next phase", "phase two", "phase 2", "later on", "later",
    "eventually", "someday", "maybe", "perhaps", "possibly", "might add",
    "might want", "consider adding", "thinking about", "not yet",
)

# Cues that read naturally *after* the service: "a database in the future",
# "a cache is not needed". Deliberately a small list -- a general backward cue
# like "no" would wrongly negate "a database and no cache" when scanned
# forwards, so only unambiguous trailing forms appear here.
POSTFIX_CUES: tuple[str, ...] = (
    "in the future", "in future", "down the line", "at some point",
    "next phase", "phase two", "phase 2", "later", "eventually", "someday",
    "is not needed", "not needed", "not required", "not yet", "is optional",
    "can wait", "would be nice",
)

# A forward scan stops at these: what follows is a different service.
POSTFIX_BOUNDARIES: tuple[str, ...] = (" and ", " with ", " plus ", " or ", ". ")

# How far forward a trailing cue may sit.
POSTFIX_WINDOW = 26

# "no single point of failure" is an availability requirement, not a refusal
# of whatever service happens to follow it.
NEGATION_EXEMPTIONS: tuple[str, ...] = (
    "no single point of failure", "no downtime", "no data loss",
    "no public access", "no public ip", "no outbound", "no internet access",
)

# How far back a cue may sit and still govern the phrase. Deliberately short:
# a cue three clauses away is describing something else.
NEGATION_WINDOW = 28

# Cutting the window here stops a refusal leaking across a contrast, so that
# "no database but a web server" does not also drop the web server.
# "for now" and "just" are the common way of saying "the deferral ended, here
# is what I actually want today", so they reset a hedge the same way "but"
# resets a negation.
CLAUSE_BOUNDARIES: tuple[str, ...] = (
    " but ", " however ", " although ", ". ",
    " for now ", " for the moment ", " right now ", " currently ",
    " initially ", " to start ", " just ", " only ", " instead ",
)

# Asking for any of these means the listener terminates TLS.
TLS_MARKERS: tuple[str, ...] = (
    "https", "tls", "ssl", "certificate", "acm", "encrypted in transit",
    "encryption in transit", "port 443", "secure connection", "secure traffic",
)

# Phrases that mean the workload should sit in private subnets.
PRIVATE_PLACEMENT_MARKERS: tuple[str, ...] = (
    "private subnet", "private subnets", "in private", "privately",
    "not publicly accessible", "no public ip", "internal only",
)

# Every word that appears inside a service phrase. Used to tell a quantity
# ("3 servers") apart from a digit that is part of a product name ("Route 53").
LEXICON_TOKENS: frozenset[str] = frozenset(
    word for entry in LEXICON for phrase in entry.phrases for word in phrase.split()
)

# Phrases that name a cloud other than AWS. Only this catalog is implemented,
# so the honest response is to say so rather than to emit AWS resources for an
# Azure request -- the user would not discover the substitution until apply.
PROVIDER_MARKERS: dict[str, tuple[str, ...]] = {
    "azure": (
        "azure", "microsoft azure", "azure vm", "azure virtual machine",
        "resource group", "storage account", "cosmos db", "cosmosdb",
        "azure functions", "aks", "azure kubernetes", "app service",
        "blob container", "azure sql", "vnet", "virtual network gateway",
    ),
    "gcp": (
        "gcp", "google cloud", "google cloud platform", "compute engine",
        "cloud run", "cloud functions", "bigquery", "gke",
        "google kubernetes engine", "cloud spanner", "cloud storage bucket",
        "firestore", "pub/sub", "cloud sql",
    ),
    "oci": ("oracle cloud", "oci ", "oracle cloud infrastructure"),
    "alibaba": ("alibaba cloud", "aliyun"),
    "ibm": ("ibm cloud",),
}

PROVIDER_DISPLAY: dict[str, str] = {
    "azure": "Microsoft Azure",
    "gcp": "Google Cloud Platform",
    "oci": "Oracle Cloud Infrastructure",
    "alibaba": "Alibaba Cloud",
    "ibm": "IBM Cloud",
}

# Phrases that confirm AWS, used to break a tie when a prompt names both --
# "migrate from Azure to AWS" is an AWS request.
AWS_MARKERS: tuple[str, ...] = (
    "aws", "amazon web services", "ec2", "s3", "rds", "lambda", "vpc",
    "cloudfront", "route 53", "dynamodb", "eks", "ecs", "elasticache",
)

# Regions the extractor recognises, keyed by how people usually write them.
REGION_ALIASES: dict[str, str] = {
    "us-east-1": "us-east-1", "n. virginia": "us-east-1", "north virginia": "us-east-1",
    "virginia": "us-east-1",
    "us-east-2": "us-east-2", "ohio": "us-east-2",
    "us-west-1": "us-west-1", "n. california": "us-west-1", "california": "us-west-1",
    "us-west-2": "us-west-2", "oregon": "us-west-2",
    "eu-west-1": "eu-west-1", "ireland": "eu-west-1",
    "eu-west-2": "eu-west-2", "london": "eu-west-2",
    "eu-central-1": "eu-central-1", "frankfurt": "eu-central-1", "germany": "eu-central-1",
    "ap-south-1": "ap-south-1", "mumbai": "ap-south-1", "india": "ap-south-1",
    "ap-southeast-1": "ap-southeast-1", "singapore": "ap-southeast-1",
    "ap-southeast-2": "ap-southeast-2", "sydney": "ap-southeast-2",
    "ap-northeast-1": "ap-northeast-1", "tokyo": "ap-northeast-1",
    "sa-east-1": "sa-east-1", "sao paulo": "sa-east-1",
    "ca-central-1": "ca-central-1", "canada": "ca-central-1",
}

# Database engines, most specific first: (phrase, rds engine, default version).
DB_ENGINES: tuple[tuple[str, str, str], ...] = (
    ("aurora postgresql", "aurora-postgresql", "15.4"),
    ("aurora mysql", "aurora-mysql", "8.0"),
    ("postgresql", "postgres", "15.5"),
    ("postgres", "postgres", "15.5"),
    ("mysql", "mysql", "8.0.35"),
    ("mariadb", "mariadb", "10.11"),
    ("sql server", "sqlserver-ex", "15.00"),
    ("oracle", "oracle-se2", "19.0"),
)

ENVIRONMENTS: tuple[str, ...] = (
    "production", "prod", "staging", "stage", "qa", "testing", "test",
    "development", "dev", "sandbox",
)

ENVIRONMENT_CANONICAL: dict[str, str] = {
    "production": "prod", "prod": "prod",
    "staging": "staging", "stage": "staging",
    "qa": "qa", "testing": "test", "test": "test",
    "development": "dev", "dev": "dev", "sandbox": "sandbox",
}
