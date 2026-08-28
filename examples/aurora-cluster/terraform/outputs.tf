# Useful values after apply.

output "vpc_id" {
  description = "ID of the generated VPC"
  value       = aws_vpc.main.id
}

output "load_balancer_dns" {
  description = "Public DNS name of the load balancer"
  value       = aws_lb.alb.dns_name
}

output "app_db_cluster_endpoint" {
  description = "Aurora cluster writer endpoint"
  value       = aws_rds_cluster.app_db_cluster.endpoint
  sensitive   = true
}

output "app_db_cluster_reader_endpoint" {
  description = "Aurora cluster reader endpoint"
  value       = aws_rds_cluster.app_db_cluster.reader_endpoint
  sensitive   = true
}
