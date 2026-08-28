# Data stores.

resource "aws_db_subnet_group" "main" {
  name       = "${local.name_prefix}-db-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = local.tags
}

# Master password for the cluster.
resource "random_password" "app_db_cluster_master" {
  length  = 32
  special = false
}

resource "aws_rds_cluster" "app_db_cluster" {
  cluster_identifier      = "${local.name_prefix}-app-db-cluster"
  engine                  = "aurora-postgresql"
  engine_version          = "15.4"
  database_name           = var.db_name
  master_username         = var.db_username
  master_password         = random_password.app_db_cluster_master.result
  port                    = 5432
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.db_sg.id]
  storage_encrypted       = true
  backup_retention_period = 7
  preferred_backup_window = "03:00-04:00"
  deletion_protection     = true
  skip_final_snapshot     = false
  tags                    = merge(local.tags, { Name = "${local.name_prefix}-aurora" })
}

# Writer plus any readers. Aurora shares one storage volume.
resource "aws_rds_cluster_instance" "app_db_cluster_instances" {
  count                        = 2
  identifier                   = "${local.name_prefix}-aurora-${count.index + 1}"
  cluster_identifier           = aws_rds_cluster.app_db_cluster.id
  instance_class               = "db.r6g.large"
  engine                       = aws_rds_cluster.app_db_cluster.engine
  engine_version               = aws_rds_cluster.app_db_cluster.engine_version
  performance_insights_enabled = true
  tags                         = local.tags
}
