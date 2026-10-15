-- 001_threads_and_rls.sql
-- Rejestr wątków + odmowa domyślna (RLS bez polityk) na całym schemacie public.
-- Idempotentne: bezpieczne do ponownego uruchomienia.
--
-- UWAGA: brak polityk jest ZAMIERZONY. Backend łączy się jako właściciel tabel
-- (RLS go nie dotyczy); role anon/authenticated nie widzą nic.
-- NIE dodawać polityk, NIE używać FORCE ROW LEVEL SECURITY.

create table if not exists threads (
  user_id    uuid        not null,
  thread_id  uuid        not null,
  label      text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, thread_id)
);

create index if not exists idx_threads_user_updated
  on threads (user_id, updated_at desc);

alter table threads                enable row level security;
alter table checkpoints            enable row level security;
alter table checkpoint_writes      enable row level security;
alter table checkpoint_blobs       enable row level security;
alter table checkpoint_migrations  enable row level security;