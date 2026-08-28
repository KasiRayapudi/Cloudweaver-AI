# Shared naming and tagging.

locals {
  name_prefix = "${var.project_name}-${var.environment}"
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

# Latest Amazon Linux 2023 AMI in the target region.
data "aws_ami" "amazon_linux" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
}
