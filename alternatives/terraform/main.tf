# The s3-blob-store Terraform route: one private bucket holding
# ciphertext blobs, fronted by a Lambda Function URL that checks a
# bearer token. The Lambda never decrypts; it stores and returns
# opaque text by label. The same resources as ../../template.yaml;
# see README.md here for the steps, ../../README.md for the design,
# and ../../SCORECARD.md for the STORE-TEST results.
#
# The backend and provider are configured in backend.tf.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Derived, not chosen: account id + region keep the globally
  # unique S3 name collision-free for the next adopter too.
  blob_bucket_name = "handoff-blobs-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.name}${var.name_suffix != "" ? "-${var.name_suffix}" : ""}"
  blob_prefix      = "blobs/"
}

# ------------------------------------------------------------------
# Blob bucket. Versioning stays OFF (the bucket default): a replaced
# or deleted blob must actually be gone through the API, or the
# STORE-TEST history check fails. This is the opposite of the
# Terraform state bucket, where versioning is on as a recovery path.
# ------------------------------------------------------------------

resource "aws_s3_bucket" "blobs" {
  bucket = local.blob_bucket_name
}

resource "aws_s3_bucket_public_access_block" "blobs" {
  bucket                  = aws_s3_bucket.blobs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "blobs" {
  bucket = aws_s3_bucket.blobs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "blobs" {
  bucket = aws_s3_bucket.blobs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# TLS only, same posture as the state bucket's policy.
resource "aws_s3_bucket_policy" "blobs" {
  bucket = aws_s3_bucket.blobs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.blobs.arn,
          "${aws_s3_bucket.blobs.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}

# ------------------------------------------------------------------
# Lambda execution role: the only identity that touches the bucket.
# Object actions only, on this bucket's blob prefix only. No IAM
# users, no access keys, nothing long-lived to leak.
# ------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  name = "handoff-blob-store-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      },
    ]
  })
}

resource "aws_iam_role_policy" "lambda_blobs" {
  name = "blob-object-access"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = "${aws_s3_bucket.blobs.arn}/${local.blob_prefix}*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ------------------------------------------------------------------
# The Lambda and its public Function URL. The URL itself is
# unauthenticated at the AWS layer (authorization_type NONE) because
# the bearer-token check happens in the handler; the permission
# below is what lets an unauthenticated request reach the handler
# at all. A request without the token gets a 401 from the code and
# never touches S3.
# ------------------------------------------------------------------

# The handler is the repo's one copy, lambda/index.py, the same file
# the CloudFormation template inlines, so the two routes can't drift.
data "archive_file" "lambda" {
  type        = "zip"
  source_file = "${path.module}/../../lambda/index.py"
  output_path = "${path.module}/.terraform/lambda.zip"
}

resource "aws_lambda_function" "blobs" {
  function_name = "handoff-blob-store"
  role          = aws_iam_role.lambda.arn
  handler       = "index.handler"
  runtime       = "python3.13"
  timeout       = 10
  memory_size   = 128

  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      BLOB_BUCKET         = aws_s3_bucket.blobs.id
      BLOB_PREFIX         = local.blob_prefix
      BEARER_TOKEN_SHA256 = var.bearer_token_sha256
    }
  }
}

resource "aws_lambda_function_url" "blobs" {
  function_name      = aws_lambda_function.blobs.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "function_url_public" {
  statement_id           = "AllowFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.blobs.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# AWS also requires a separate lambda:InvokeFunction permission
# before a NONE-auth Function URL will serve requests at all. With
# only the permission above, every request — authenticated or not —
# gets a 403 from AWS before the handler runs (observed live
# 2026-11-20). AWS's own console scopes this grant with a
# lambda:FunctionUrlAuthType=NONE condition. The AWS provider this
# route pins (~> 5.0) can't express that on the InvokeFunction action
# (it rejects function_url_auth_type for it, observed 2026-11-20);
# provider 6.28.0 added invoked_via_function_url, which the
# CloudFormation template's equivalent uses; moving this route to
# it is the provider-6 item in backlog #27 (formerly #20). So here
# the grant is unconditional at the AWS
# layer. The handler's bearer-token check is therefore the
# actual gate for every invocation path, which the design already
# assumes: the function holds nothing but ciphertext access, and
# returns 401 before touching S3.
resource "aws_lambda_permission" "function_url_invoke" {
  statement_id  = "AllowFunctionUrlInvokeFunction"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.blobs.function_name
  principal     = "*"
}
