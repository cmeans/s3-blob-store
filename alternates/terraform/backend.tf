# Terraform backend and provider for the s3-blob-store Terraform route.
#
# State goes in a Terraform state bucket: one you already have, or
# the shared one ../bootstrap/bootstrap.yaml creates. The bucket and
# region are left out on purpose (a partial backend configuration), so
# this file needs no edits: pass them to init (for the bootstrap
# stack, its StateBucketName and Region outputs):
#
#   terraform init \
#     -backend-config="bucket=<StateBucketName output>" \
#     -backend-config="region=<Region output>"
#
# It contains no secrets. Terraform 1.11 or later is required (for
# use_lockfile).

terraform {
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    # bucket and region: from -backend-config at init (see above).

    # State object path inside that bucket. One key per project/stack,
    # so other Terraform projects can share the bucket without colliding.
    key = "s3-blob-store/terraform.tfstate"

    # Encrypt the state object at rest.
    encrypt = true

    # Native S3 state locking (Terraform >= 1.11).
    # This replaces the deprecated DynamoDB locking table:
    # do NOT add a dynamodb_table argument.
    use_lockfile = true
  }
}

# The region the store's resources are created in. It needn't match the
# state bucket's region.
provider "aws" {
  region = var.region
}
