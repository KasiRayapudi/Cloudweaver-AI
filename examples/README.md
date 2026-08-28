# Example outputs

Committed output for three prompts, regenerated with:

```bash
python backend/cli.py "<prompt>" -o examples/<name>
```

| Directory | Prompt |
|---|---|
| `single-ec2/` | One Ubuntu EC2 instance in a VPC with an internet gateway, a security group allowing SSH and HTTP, an IAM role and an Elastic IP. |
| `three-tier-web-app/` | Production three-tier app in eu-west-1: auto scaling group in private subnets behind an ALB, Multi-AZ PostgreSQL, Redis cache, S3 uploads bucket, highly available. |
| `serverless-api/` | Serverless REST API: API Gateway → Python Lambda → DynamoDB, with an SQS queue for background jobs, dev in ap-south-1. |
| `static-site-cdn/` | Static site in S3 behind CloudFront with a Route 53 domain and a WAF. |

`single-ec2/` is the one to read first. It contains eight resources and no
others: no load balancer, no scaling group, no NAT gateway, no database. The
AMI is Ubuntu because the prompt said Ubuntu, and the security group opens 22
and 80 because the prompt said SSH and HTTP.

Each directory holds `terraform/`, `diagram/architecture.svg`,
`diagram/architecture.mmd` and `spec.json` — the shared model the other two
were generated from.

Worth noticing across the three: the serverless and static-site designs contain
no VPC, subnets or NAT gateway at all. The mapper only creates network
infrastructure when something in the design actually needs to live inside it,
so the generated project stays proportional to what was asked for.
