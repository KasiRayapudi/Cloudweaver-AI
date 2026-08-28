# Useful values after apply.

output "vpc_id" {
  description = "ID of the generated VPC"
  value       = aws_vpc.main.id
}
