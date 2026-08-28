# Useful values after apply.

output "vpc_id" {
  description = "ID of the generated VPC"
  value       = aws_vpc.main.id
}

output "load_balancer_dns" {
  description = "Public DNS name of the load balancer"
  value       = aws_lb.alb.dns_name
}

output "database_endpoint" {
  description = "Connection endpoint for the managed database"
  value       = aws_db_instance.app_db.address
  sensitive   = true
}

output "assets_bucket" {
  description = "Name of the S3 Bucket"
  value       = aws_s3_bucket.assets.id
}
