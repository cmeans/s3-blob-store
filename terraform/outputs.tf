# What the operator needs after apply. None of these are secrets:
# the function URL is a host name anyone can resolve; reaching the
# blobs still requires the bearer token.

output "blob_bucket_name" {
  description = "Name of the blob bucket (versioning off)."
  value       = aws_s3_bucket.blobs.id
}

output "function_url" {
  description = "Base URL of the blob front door. Blobs live at <url>blobs/<label>."
  value       = aws_lambda_function_url.blobs.function_url
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role (the only identity with blob access)."
  value       = aws_iam_role.lambda.arn
}
