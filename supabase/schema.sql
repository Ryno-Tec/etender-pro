-- Tender tracker for tenders.ryno-tec.co.za (Ryno-Tec and Lesego Laka Projects).
-- Run this once in Supabase: SQL Editor -> New query -> paste -> Run. Safe to run again.
--
-- The GitHub Action fills the table with the service_role key (tools/supabase_sync.py).
-- Signed-in team members can read every tender and change only its status and notes.

create table if not exists public.tenders (
  ocid           text primary key,
  company        text not null check (company in ('rtc', 'llp')),  -- rtc = Ryno-Tec, llp = Lesego Laka Projects
  title          text not null default '',
  tender_no      text not null default '',
  buyer          text not null default '',
  province       text not null default '',
  category       text not null default '',
  value          numeric,
  closing        timestamptz,
  briefing       text not null default 'Not stated',
  compulsory     boolean not null default false,
  briefing_at    timestamptz,
  briefing_venue text not null default '',
  briefing_link  text not null default '',
  source_url     text not null default '',
  docs_url       text not null default '',
  status         text not null default 'New',
  notes          text not null default '',
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index if not exists tenders_company_closing on public.tenders (company, closing);

alter table public.tenders enable row level security;

drop policy if exists "team reads tenders" on public.tenders;
create policy "team reads tenders" on public.tenders for select to authenticated using (true);
drop policy if exists "team updates tenders" on public.tenders;
create policy "team updates tenders" on public.tenders for update to authenticated using (true) with check (true);

-- the website may only change status and notes; nobody signed out can see anything
revoke all on public.tenders from anon, authenticated;
grant select on public.tenders to authenticated;
grant update (status, notes, updated_at) on public.tenders to authenticated;

-- Retention: tenders are like adverts. Delete the ones nobody worked on
-- (status still 'New' and no notes) once they closed more than `days` days ago.
create or replace function public.purge_expired_tenders(days int default 7)
returns int
language sql
security definer
set search_path = public
as $$
  with gone as (
    delete from public.tenders
    where status = 'New'
      and btrim(notes) = ''
      and closing is not null
      and closing < now() - make_interval(days => days)
    returning 1
  )
  select count(*)::int from gone;
$$;

revoke execute on function public.purge_expired_tenders(int) from public, anon, authenticated;
grant execute on function public.purge_expired_tenders(int) to service_role;
