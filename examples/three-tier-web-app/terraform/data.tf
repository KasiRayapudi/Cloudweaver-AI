# Data stores.

resource "aws_db_subnet_group" "main" {
  name       = "${local.name_prefix}-db-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = local.tags
}

# Master password, generated rather than written down.
resource "random_password" "app_db_password" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_db_instance" "app_db" {
  identifier                   = "${local.name_prefix}-app-db"
  engine                       = "postgres"
  engine_version               = "15.5"
  instance_class               = "db.t3.medium"
  allocated_storage            = 20
  storage_type                 = "gp3"
  storage_encrypted            = true
  db_name                      = var.db_name
  username                     = var.db_username
  password                     = random_password.app_db_password.result
  port                         = 5432
  db_subnet_group_name         = aws_db_subnet_group.main.name
  vpc_security_group_ids       = [aws_security_group.db_sg.id]
  multi_az                     = true
  publicly_accessible          = false
  backup_retention_period      = 7
  deletion_protection          = true
  skip_final_snapshot          = false
  auto_minor_version_upgrade   = true
  performance_insights_enabled = true
  tags                         = merge(local.tags, { Name = "${local.name_prefix}-db" })
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${local.name_prefix}-cache-subnets"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "app_cache" {
  replication_group_id       = "${local.name_short}-redis"
  description                = "Redis cache generated from the requirement description"
  engine                     = "redis"
  node_type                  = "cache.t3.micro"
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.cache_sg.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  tags                       = local.tags
}

resource "aws_s3_bucket" "assets" {
  bucket = "${local.name_prefix}-assets-${random_id.bucket_suffix.hex}"
  tags   = local.tags
}

resource "aws_s3_bucket_versioning" "assets_versioning" {
  bucket = aws_s3_bucket.assets.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "assets_encryption" {
  bucket = aws_s3_bucket.assets.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "assets_access" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 bucket names are globally unique; add a stable suffix.
resource "random_id" "bucket_suffix" {
  byte_length = 4
}
