# Compute resources.

# Package the handler source at plan time -- no build step needed.
data "archive_file" "lambda_fn" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = "${path.module}/build/lambda_fn.zip"
}

resource "aws_lambda_function" "lambda_fn" {
  function_name    = "${local.name_prefix}-lambda-fn"
  role             = aws_iam_role.lambda_role.arn
  handler          = "index.handler"
  runtime          = "python3.12"
  memory_size      = 512
  timeout          = 30
  filename         = data.archive_file.lambda_fn.output_path
  source_code_hash = data.archive_file.lambda_fn.output_base64sha256

  environment {
    variables = {
      ENVIRONMENT = var.environment
      TABLE_NAME  = aws_dynamodb_table.app_table.name
      QUEUE_URL   = aws_sqs_queue.work_queue.url
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_log_group" "lambda_fn_logs" {
  name              = "/aws/lambda/${aws_lambda_function.lambda_fn.function_name}"
  retention_in_days = 14
  tags              = local.tags
}
