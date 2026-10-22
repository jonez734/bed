-- Grants for the bed service role. Run by the owner of engine.pgrole. Idempotent.
--
-- bed reads the member-role tracking table, to find a member's role.
-- bed holds no standing membership in any member role (including jam):
-- it must not act as other roles.
GRANT SELECT ON engine.pgrole TO bed;

-- Install-only privileges. The service does not run DDL: bed --install runs
-- as the owner, so bed needs neither REFERENCES on the member table nor
-- CREATE on the database at runtime.
REVOKE REFERENCES ON engine.__member FROM bed;
DO $$
BEGIN
    EXECUTE format('REVOKE CREATE ON DATABASE %I FROM bed', current_database());
END
$$;

-- bed does not need sysop. Its functions check sysop through the zoid6-owned
-- wrapper bed_is_sysop, not through role membership. Revoke any earlier grant.
REVOKE sysop FROM bed;
