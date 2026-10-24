# Scheduled jobs + alarms.

resource "aws_iam_role" "events" {
  name = "${local.name}-events"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "events.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "events_run_task" {
  role = aws_iam_role.events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:RunTask"], Resource = [aws_ecs_task_definition.api.arn_without_revision, "${aws_ecs_task_definition.api.arn_without_revision}:*"] },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn] },
    ]
  })
}

locals {
  jobs = {
    retention-purge  = { schedule = "cron(30 2 * * ? *)", command = ["callbridge", "purge"] }
    rollout-evaluate = { schedule = "rate(10 minutes)", command = ["callbridge", "improve", "evaluate"] }
    improve-weekly   = { schedule = "cron(0 6 ? * MON *)", command = ["callbridge", "improve", "run", "--days", "7"] }
  }
}

resource "aws_cloudwatch_event_rule" "job" {
  for_each            = local.jobs
  name                = "${local.name}-${each.key}"
  schedule_expression = each.value.schedule
}

resource "aws_cloudwatch_event_target" "job" {
  for_each = local.jobs
  rule     = aws_cloudwatch_event_rule.job[each.key].name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.events.arn
  ecs_target {
    task_definition_arn = aws_ecs_task_definition.api.arn
    launch_type         = "FARGATE"
    network_configuration {
      subnets         = aws_subnet.private[*].id
      security_groups = [aws_security_group.app.id]
    }
  }
  input = jsonencode({ containerOverrides = [{ name = "api", command = each.value.command }] })
}

resource "aws_sns_topic" "alerts" {
  name              = "${local.name}-alerts"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name}-api-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.main.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_unhealthy" {
  alarm_name          = "${local.name}-api-unhealthy"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  dimensions          = { LoadBalancer = aws_lb.main.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_cpu" {
  alarm_name          = "${local.name}-db-cpu"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

output "api_url" { value = "https://${var.domain}" }
output "twilio_voice_webhook" { value = "https://${var.domain}/twilio/voice" }
output "alb_dns_name" { value = aws_lb.main.dns_name }
output "ecr_api" { value = aws_ecr_repository.api.repository_url }
output "ecr_dashboard" { value = aws_ecr_repository.dashboard.repository_url }
output "db_endpoint" { value = aws_db_instance.main.address }
output "app_secret_arn" { value = aws_secretsmanager_secret.app.arn }
