# Messaging and eventing.

# Dead-letter queue for messages that fail repeatedly.
resource "aws_sqs_queue" "work_queue_dlq" {
  name                      = "${local.name_prefix}-work-queue-dlq"
  message_retention_seconds = 1209600
  tags                      = local.tags
}

resource "aws_sqs_queue" "work_queue" {
  name                       = "${local.name_prefix}-work-queue"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 345600
  redrive_policy             = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.work_queue_dlq.arn
    maxReceiveCount     = 5
  })
  tags                       = local.tags
}

resource "aws_lambda_event_source_mapping" "work_queue_lambda" {
  event_source_arn = aws_sqs_queue.work_queue.arn
  function_name    = aws_lambda_function.lambda_fn.arn
  batch_size       = 10
}
