# Useful values after apply.

output "cdn_domain" {
  description = "CloudFront distribution domain"
  value       = aws_cloudfront_distribution.cdn.domain_name
}

output "assets_bucket" {
  description = "Name of the S3 Bucket"
  value       = aws_s3_bucket.assets.id
}
