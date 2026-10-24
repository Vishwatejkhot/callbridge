# Callbridge on AWS London (eu-west-2). One root, two workspaces/environments:
#   terraform init -backend-config=envs/staging.backend.hcl
#   terraform apply -var-file=envs/staging.tfvars
# Everything lives in eu-west-2 so patient data stays in the UK.

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  backend "s3" {}
}

provider "aws" {
  region = "eu-west-2"
  default_tags {
    tags = { Project = "callbridge", Environment = var.env, ManagedBy = "terraform", DataResidency = "UK" }
  }
}

variable "env" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.env)
    error_message = "env must be staging or production"
  }
}
variable "domain" { type = string }           # e.g. api.callbridge.example
variable "dashboard_domain" { type = string } # e.g. app.callbridge.example
variable "certificate_arn" { type = string }  # ACM cert covering both domains (eu-west-2)
variable "image_tag" { type = string }        # git sha, set by scripts/deploy.sh
variable "api_desired_count" { default = 2 }
variable "api_cpu" { default = 1024 }
variable "api_memory" { default = 2048 }
variable "db_instance_class" { default = "db.t4g.medium" }
variable "db_multi_az" { default = false }
variable "alert_email" { type = string }
variable "enable_sim_api" { default = false }

locals {
  name = "callbridge-${var.env}"
  azs  = ["eu-west-2a", "eu-west-2b"]
}

data "aws_caller_identity" "me" {}
