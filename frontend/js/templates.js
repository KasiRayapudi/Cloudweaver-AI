/**
 * Starting points offered in the gallery and the command palette.
 *
 * Each prompt is one the generator handles well and that demonstrates a
 * different shape, so the gallery doubles as a tour of what the system can
 * express. They are deliberately written the way a person would ask.
 */

export const TEMPLATES = [
  {
    id: "three-tier",
    title: "Three-tier web application",
    icon: "layers",
    description:
      "Auto scaling web tier behind an application load balancer, a Multi-AZ database and object storage.",
    tags: ["Production", "High availability"],
    prompt:
      "A production three-tier web app in eu-west-1: an auto scaling group of EC2 " +
      "instances in private subnets behind an application load balancer with HTTPS, " +
      "a Multi-AZ PostgreSQL database, a Redis cache and an S3 bucket for uploads. " +
      "Highly available.",
  },
  {
    id: "single-ec2",
    title: "Single instance",
    icon: "server",
    description:
      "One Ubuntu machine with the minimum network around it. Nothing else — the smallest useful design.",
    tags: ["Development"],
    prompt:
      "Create a development environment in us-east-1 with one Ubuntu EC2 instance " +
      "inside a VPC. Add an Internet Gateway, Security Group allowing SSH and HTTP, " +
      "IAM role, and Elastic IP.",
  },
  {
    id: "serverless",
    title: "Serverless API",
    icon: "bolt",
    description:
      "API Gateway, a Lambda function and DynamoDB, with a queue for background work. No VPC.",
    tags: ["Serverless"],
    prompt:
      "A serverless REST API: API Gateway in front of a Python Lambda function that " +
      "reads and writes a DynamoDB table, with an SQS queue for background jobs. " +
      "Development environment in ap-south-1.",
  },
  {
    id: "containers",
    title: "Containerised service",
    icon: "layers",
    description:
      "ECS Fargate behind a load balancer, pulling images from ECR with a managed database.",
    tags: ["Containers", "Production"],
    prompt:
      "A production ECS Fargate service behind an application load balancer with " +
      "HTTPS, pulling images from ECR, using an Aurora PostgreSQL cluster in " +
      "private subnets. Highly available.",
  },
  {
    id: "kubernetes",
    title: "Kubernetes platform",
    icon: "route",
    description:
      "An EKS cluster with worker nodes, a bastion for admin access and artifact storage.",
    tags: ["Kubernetes"],
    prompt:
      "An EKS cluster with 4 t3.medium worker nodes in private subnets, an ingress " +
      "load balancer, a bastion host for admin access and an S3 bucket for artifacts. " +
      "Production in us-west-2.",
  },
  {
    id: "static-site",
    title: "Static site on a CDN",
    icon: "chart",
    description:
      "S3 behind CloudFront with a custom domain and a web application firewall.",
    tags: ["Edge"],
    prompt:
      "Host a static website in an S3 bucket served through CloudFront with a " +
      "Route 53 custom domain and a WAF in front of it.",
  },
  {
    id: "existing-vpc",
    title: "Deploy into an existing VPC",
    icon: "shield",
    description:
      "Adds to infrastructure that already exists: the VPC is read, not recreated.",
    tags: ["Brownfield"],
    prompt:
      "An EC2 instance called web-01 and a security group in my existing VPC " +
      "vpc-0abc123def456, allowing SSH and HTTPS.",
  },
  {
    id: "data-platform",
    title: "Analytics warehouse",
    icon: "chart",
    description:
      "A Redshift warehouse in private subnets with a staging bucket and encryption key.",
    tags: ["Analytics"],
    prompt:
      "A Redshift data warehouse in private subnets with an S3 staging bucket, " +
      "a KMS key and CloudWatch monitoring. Production in us-east-1.",
  },
];

/** Short prompts offered as chips under the composer. */
export const SUGGESTIONS = [
  "Two web servers behind a load balancer with a PostgreSQL database",
  "A Lambda function triggered by an SQS queue",
  "An EKS cluster with 3 nodes and an ingress load balancer",
  "A network load balancer with TLS on port 443",
  "An S3 bucket behind CloudFront with a custom domain",
];
