-- sql/003_revoke_api_grants.sql — backend uses the owner role; API roles need nothing.
revoke all on table checkpoints, checkpoint_writes, checkpoint_blobs, checkpoint_migrations,
                    threads, runs, run_events
  from anon, authenticated, service_role;

alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated, service_role;
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated, service_role;