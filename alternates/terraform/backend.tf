# Terraform backend for the muse-field-kit S3 blob-store test.
#
# This file points Terraform at the state bucket created by
# ../bootstrap/bootstrap.yaml. It contains no secrets.
#
# Before running `terraform init`:
#   1. Replace "bucket" with the StateBucketName output of the
#      bootstrap stack (the name you chose, not a guess).
#   2. Replace "region" if you deployed the bootstrap stack in a
#      region other than us-east-2.
#   3. Use Terraform 1.11 or later (required for use_lockfile).

terraform {
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    # Must match the bootstrap stack's StateBucketName output exactly.
    # With the bootstrap's default naming that output is
    # tfstate-<your AWS account id>-us-east-2 (S3 names are global,
    # so the account id is what keeps it collision-free).
    bucket = "tfstate-223988092080-us-east-2"

    # State object path inside that bucket. One key per project/stack,
    # so other Terraform projects can share the bucket without colliding.
    key = "muse-field-kit/handoff-blob-store/terraform.tfstate"

    # Must match the region the bootstrap stack was deployed in.
    region = "us-east-2"

    # Encrypt the state object at rest.
    encrypt = true

    # Native S3 state locking (Terraform >= 1.11).
    # This replaces the deprecated DynamoDB locking table:
    # do NOT add a dynamodb_table argument.
    use_lockfile = true
  }
}

# The provider region should match the backend region unless a
# resource in main.tf says otherwise.
provider "aws" {
  region = "us-east-2"
}
