-- 002_runs_and_events.sql
-- Run registry and persistent event log for resumable streams.
-- Idempotent: safe to re-run.
--
-- RLS is enabled with NO policies on purpose: the backend connects as the
-- table owner (RLS does not apply), anon/authenticated roles see nothing.
-- Do NOT add policies, do NOT use FORCE ROW LEVEL SECURITY.

create table if not exists runs (
  run_id      uuid        primary key,
  user_id     uuid        not null,
  thread_id   uuid        not null,
  kind        text        not null check (kind in ('message', 'resume', 'finish')),
  status      text        not null default 'running'
              check (status in ('running', 'done', 'waiting_gate', 'error', 'cancelled', 'interrupted')),
  last_seq    bigint      not null default 0,
  created_at  timestamptz not null default now(),
  finished_at timestamptz
);

-- Serves "latest run of a thread" for /state.
create index if not exists idx_runs_thread_recent
  on runs (user_id, thread_id, created_at desc);

-- At most one running run per thread, enforced by the database.
create unique index if not exists uq_runs_one_running_per_thread
  on runs (user_id, thread_id) where status = 'running';

create table if not exists run_events (
  run_id     uuid        not null references runs (run_id) on delete cascade,
  seq        bigint      not null,
  event_type text        not null,
  payload    jsonb       not null,
  ts         timestamptz not null default now(),
  primary key (run_id, seq)            -- also serves replay: where run_id = $1 and seq > $2 order by seq
);

alter table runs       enable row level security;
alter table run_events enable row level security;