-- Row-level security: a connection only ever sees rows for the clinic set in app.clinic_id.
-- The API runs as role `callbridge_app` (no BYPASSRLS). Cross-clinic jobs (improvement loop,
-- retention purge) run as `callbridge_ops`, which has BYPASSRLS and is never exposed to callers.
-- If app.clinic_id is unset, NULLIF(...) makes the policy match nothing: the safe default.
-- staff_users is deliberately not row-scoped: login must find the user before the clinic is known.
-- It is only ever queried by exact email, and holds no patient data.
-- Idempotent: safe to run on every deploy.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['practitioners','opening_hours','patients','bookings','memory_facts',
                           'knowledge_chunks','calls','messages','sms_log','audit_log']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS clinic_isolation ON %I', t);
    EXECUTE format(
      'CREATE POLICY clinic_isolation ON %I USING (clinic_id = NULLIF(current_setting(''app.clinic_id'', true), '''')::int) '
      'WITH CHECK (clinic_id = NULLIF(current_setting(''app.clinic_id'', true), '''')::int)', t);
  END LOOP;
END $$;

-- The audit log is append-only for the application role.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'callbridge_app') THEN
    REVOKE UPDATE, DELETE ON audit_log FROM callbridge_app;
  END IF;
END $$;
