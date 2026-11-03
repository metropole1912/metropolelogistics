-- =====================================================================
-- Metropole Logistics — v3 migration: regions (South America), oil demand, vessel tracker
-- Run in Supabase > SQL Editor on any existing database, then run the new seed.sql.
-- Includes the energy migration, so it works whether or not migrate_energy.sql was run. Safe to re-run.
-- =====================================================================

-- ---------- PART 1: energy columns (skipped automatically if already there) ----------

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

-- ---------- PART 2: regions, demand, vessels ----------
-- ---------- Regions ----------
-- africa | americas | all  (lanes and straits shared by both regions carry 'all')
alter table countries   add column if not exists region text not null default 'africa' check (region in ('africa','americas','all'));
alter table assets      add column if not exists region text not null default 'africa' check (region in ('africa','americas','all'));
alter table corridors   add column if not exists region text not null default 'africa' check (region in ('africa','americas','all'));
alter table chokepoints add column if not exists region text not null default 'africa' check (region in ('africa','americas','all'));
alter table zones       add column if not exists region text not null default 'africa' check (region in ('africa','americas','all'));
alter table zones       add column if not exists sector text not null default 'metals' check (sector in ('metals','energy','all'));
alter table events      add column if not exists region text not null default 'africa' check (region in ('africa','americas','all'));
alter table forecasts   add column if not exists region text not null default 'africa' check (region in ('africa','americas','all'));
alter table corridors   drop constraint if exists corridors_sector_check;
alter table corridors   add constraint corridors_sector_check check (sector in ('metals','energy','all'));
alter table assets      drop constraint if exists assets_sector_check;
alter table assets      add constraint assets_sector_check check (sector in ('metals','energy','all'));
alter table forecasts   drop constraint if exists forecasts_sector_check;
alter table forecasts   add constraint forecasts_sector_check check (sector in ('metals','energy','all'));
alter table subscribers add column if not exists regions text[] not null default '{}';   -- empty = all

-- ---------- Oil demand (JODI or desk snapshot) ----------
create table if not exists demand (
  iso      text not null,                 -- ISO3
  year     int  not null,
  product  text not null default 'total', -- total | crude | gasoline | diesel | jet | lpg | fuel_oil
  flow     text not null default 'demand',
  kbd      numeric not null,              -- thousand barrels per day (yearly average)
  months   int not null default 12,       -- months of data behind the yearly figure
  source   text not null default 'JODI',
  updated_at timestamptz not null default now(),
  primary key (iso, year, product, flow)
);
alter table demand enable row level security;
drop policy if exists "public read" on demand; create policy "public read" on demand for select to anon, authenticated using (true);
grant select on demand to anon, authenticated;
grant all on demand to service_role;

-- ---------- Vessels (AIS relay) ----------
create table if not exists vessels (
  mmsi        bigint primary key,
  imo         bigint,
  name        text,
  callsign    text,
  type_code   int,
  type_name   text,
  vclass      text,                        -- crude | product | lng | lpg | bulk | container | general | other
  size_class  text,                        -- VLCC, Suezmax, Capesize, ...
  est_dwt     int,
  length      int,
  width       int,
  draught     numeric,
  laden       boolean,
  flag        text,                        -- ISO2 from the MMSI MID
  destination text,
  eta         text,
  lon         double precision,
  lat         double precision,
  sog         numeric,
  cog         numeric,
  heading     int,
  nav_status  int,
  last_seen   timestamptz,
  first_seen  timestamptz not null default now(),
  sanctioned  boolean not null default false,
  sanctions_program text,
  note        text
);
create index if not exists vessels_last_seen on vessels (last_seen desc);
create index if not exists vessels_sanctioned on vessels (sanctioned) where sanctioned;
alter table vessels enable row level security;
drop policy if exists "public read" on vessels; create policy "public read" on vessels for select to anon, authenticated using (true);
grant select on vessels to anon, authenticated;
grant all on vessels to service_role;

-- Forget vessels not heard from in 14 days (run by the relay with --prune, or by hand)
create or replace function prune_vessels(days int default 14) returns int language sql as $$
  with d as (delete from vessels where last_seen < now() - make_interval(days => days) returning 1) select count(*)::int from d;
$$;
