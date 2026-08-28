# Values for this deployment. Edit before running `terraform apply`.
project_name = "production-three-tier-prod"
environment  = "prod"
region       = "eu-west-1"
vpc_cidr     = "10.0.0.0/16"
az_count     = 3
db_username  = "appadmin"
db_name      = "appdb"
