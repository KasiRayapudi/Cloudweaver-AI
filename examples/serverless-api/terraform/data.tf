# Data stores.

resource "aws_dynamodb_table" "app_table" {
  name         = "${local.name_prefix}-app-table"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }

  server_side_encryption {
    enabled = true
  }
  tags = local.tags
}
