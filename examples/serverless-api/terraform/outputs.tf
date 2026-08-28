# Useful values after apply.

output "api_endpoint" {
  description = "Base URL of the HTTP API"
  value       = aws_apigatewayv2_api.api_gateway.api_endpoint
}
