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
