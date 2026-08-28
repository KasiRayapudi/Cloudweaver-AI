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

# Latest ubuntu AMI in the target region.
data "aws_ami" "os" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}
