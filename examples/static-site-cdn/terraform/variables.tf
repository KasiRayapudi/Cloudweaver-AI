# Input variables. Override them in terraform.tfvars.

# Name prefix applied to every resource.
variable "project_name" {
  type    = string
  default = "host-static-website"
}

# Deployment environment (dev, staging, prod).
variable "environment" {
  type    = string
  default = "dev"
}

# AWS region to deploy into.
variable "region" {
  type    = string
  default = "us-east-1"
}

# Extra tags merged into every resource.
variable "tags" {
  type    = map(string)
  default = {}
}
