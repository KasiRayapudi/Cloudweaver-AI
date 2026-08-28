# CloudWatch alarms.

resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"
  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "${local.name_prefix}-high-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "Average CPU above 80% for 10 minutes"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  dimensions          = {
    AutoScalingGroupName = aws_autoscaling_group.app_asg.name
  }
  tags                = local.tags
}

resource "aws_cloudwatch_metric_alarm" "db_storage" {
  alarm_name          = "${local.name_prefix}-db-low-storage"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 2000000000
  alarm_description   = "Less than 2 GB of free database storage"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  dimensions          = {
    DBInstanceIdentifier = aws_db_instance.app_db.id
  }
  tags                = local.tags
}
