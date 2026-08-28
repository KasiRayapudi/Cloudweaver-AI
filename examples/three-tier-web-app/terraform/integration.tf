# Messaging and eventing.

resource "aws_secretsmanager_secret" "db_secret" {
  name                    = "${local.name_prefix}-db-secret"
  description             = "Master credentials for the managed database"
  recovery_window_in_days = 7
  tags                    = local.tags
}

resource "aws_secretsmanager_secret_version" "db_secret_value" {
  secret_id     = aws_secretsmanager_secret.db_secret.id
  secret_string = jsonencode({
    username = var.db_username
    password = random_password.db.result
    host     = aws_db_instance.app_db.address
    port     = aws_db_instance.app_db.port
    dbname   = var.db_name
  })
}
