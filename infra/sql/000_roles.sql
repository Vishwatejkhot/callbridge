-- Run once per environment as the RDS master user (Terraform does this via a bootstrap task).
-- Passwords come from AWS Secrets Manager, never from this file.
CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'callbridge_app') THEN
    CREATE ROLE callbridge_app LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'callbridge_ops') THEN
    CREATE ROLE callbridge_ops LOGIN BYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO callbridge_app, callbridge_ops;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO callbridge_app, callbridge_ops;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO callbridge_app, callbridge_ops;
