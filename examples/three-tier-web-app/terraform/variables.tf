# Input variables. Override them in terraform.tfvars.

# Name prefix applied to every resource.
variable "project_name" {
  type    = string
  default = "production-three-tier-prod"
}

# Deployment environment (dev, staging, prod).
variable "environment" {
  type    = string
  default = "prod"
}

# AWS region to deploy into.
variable "region" {
  type    = string
  default = "eu-west-1"
}

# CIDR block for the VPC.
variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

# Number of availability zones to span.
variable "az_count" {
  type    = number
  default = 3
}

# Master username for the managed database.
variable "db_username" {
  type    = string
  default = "appadmin"
}

# Initial database name.
variable "db_name" {
  type    = string
  default = "appdb"
}

# Existing EC2 key pair for SSH access.
variable "key_pair_name" {
  type    = string
  default = null
}

# Extra tags merged into every resource.
variable "tags" {
  type    = map(string)
  default = {}
}
