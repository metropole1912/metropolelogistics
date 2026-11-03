-- =====================================================================
-- Metropole Logistics — energy sector migration
-- Run this ONCE in Supabase > SQL Editor on a database created with the
-- original schema.sql, then run the new seed.sql. Safe to re-run.
-- =====================================================================

alter table assets drop constraint if exists assets_kind_check;
alter table assets add constraint assets_kind_check
  check (kind in ('mine','plant','field','refinery','lng','terminal','storage'));
alter table assets add column if not exists sector text not null default 'metals' check (sector in ('metals','energy'));

alter table corridors drop constraint if exists corridors_mode_check;
alter table corridors add constraint corridors_mode_check check (mode in ('rail','road','mixed','pipeline','sea'));
alter table corridors add column if not exists sector text not null default 'metals' check (sector in ('metals','energy'));

alter table chokepoints add column if not exists sector text not null default 'metals' check (sector in ('metals','energy','all'));
alter table countries   add column if not exists sectors text[] not null default '{metals}';
alter table events      add column if not exists sector text not null default 'metals' check (sector in ('metals','energy','all'));
alter table forecasts   add column if not exists sector text not null default 'metals' check (sector in ('metals','energy'));
alter table subscribers add column if not exists sectors text[] not null default '{}';
alter table sanctions_hits add column if not exists entry_type text not null default 'entity';

-- Daily market prices pulled by the collector (EIA and others)
create table if not exists prices (
  series   text not null,          -- e.g. brent, wti, diesel_nyh
  date     date not null,
  value    numeric not null,
  unit     text not null default 'USD/bbl',
  source   text not null default 'EIA',
  fetched_at timestamptz not null default now(),
  primary key (series, date)
);
alter table prices enable row level security;
drop policy if exists "public read" on prices; create policy "public read" on prices for select to anon, authenticated using (true);
grant select on prices to anon, authenticated;
grant all on prices to service_role;
