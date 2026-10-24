# Postgres 17 + pgvector on RDS: encrypted at rest, TLS enforced, daily backups kept 14 days,
# deletion protection in production. Restore drill: docs/runbook.md#restore-test.

resource "random_password" "db_master" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "pg" {
  name   = "${local.name}-pg17"
  family = "postgres17"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "500"
  }
}

resource "aws_kms_key" "data" {
  description         = "${local.name} data at rest"
  enable_key_rotation = true
}

resource "aws_db_instance" "main" {
  identifier                   = local.name
  engine                       = "postgres"
  engine_version               = "17"
  instance_class               = var.db_instance_class
  allocated_storage            = 20
  max_allocated_storage        = 200
  storage_encrypted            = true
  kms_key_id                   = aws_kms_key.data.arn
  db_name                      = "callbridge"
  username                     = "callbridge_admin"
  password                     = random_password.db_master.result
  db_subnet_group_name         = aws_db_subnet_group.main.name
  vpc_security_group_ids       = [aws_security_group.db.id]
  parameter_group_name         = aws_db_parameter_group.pg.name
  multi_az                     = var.db_multi_az
  backup_retention_period      = 14
  backup_window                = "02:00-03:00"
  maintenance_window           = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot        = true
  deletion_protection          = var.env == "production"
  skip_final_snapshot          = var.env != "production"
  final_snapshot_identifier    = "${local.name}-final"
  performance_insights_enabled = true
  auto_minor_version_upgrade   = true
}

# ---- Secrets: the app reads these at task start; nothing secret is in images or tfvars.

resource "aws_secretsmanager_secret" "app" {
  name       = "${local.name}/app"
  kms_key_id = aws_kms_key.data.arn
}

resource "aws_secretsmanager_secret" "db_master" {
  name       = "${local.name}/db-master"
  kms_key_id = aws_kms_key.data.arn
}

resource "aws_secretsmanager_secret_version" "db_master" {
  secret_id     = aws_secretsmanager_secret.db_master.id
  secret_string = jsonencode({ username = "callbridge_admin", password = random_password.db_master.result, host = aws_db_instance.main.address })
}

# The app secret's value (JSON with CALLBRIDGE_DATABASE_URL for callbridge_app, ANTHROPIC_API_KEY, ...)
# is set once by an operator with `aws secretsmanager put-secret-value`, never through Terraform state.
locals {
  app_secret_keys = [
    "CALLBRIDGE_DATABASE_URL", "CALLBRIDGE_OPS_DATABASE_URL", "CALLBRIDGE_JWT_SECRET", "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY", "GROQ_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN", "SENTRY_DSN", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
  ]
}
