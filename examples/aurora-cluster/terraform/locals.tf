# Shared naming and tagging.

locals {
  name_prefix = "${var.project_name}-${var.environment}"
  name_short  = trimsuffix(substr("${var.project_name}-${var.environment}", 0, 26), "-")
  tags        = merge({
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    GeneratedBy = "ai-infra-iac-generator"
  }, var.tags)
}

data "aws_availability_zones" "available" {
  state = "available"
}

# Latest amazon linux AMI in the target region.
data "aws_ami" "os" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}
