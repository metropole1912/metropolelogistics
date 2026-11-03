-- =====================================================================
-- Metropole Logistics — database schema (Supabase / PostgreSQL)
-- Paste this whole file into Supabase > SQL Editor and click Run.
-- Safe to run more than once.
-- =====================================================================

-- ---------- Reference data (what the map draws) ----------

create table if not exists countries (
  iso          text primary key,              -- ISO3 code, e.g. COD
  iso_num      text not null,                 -- numeric code used by the map boundaries, e.g. 180
  name         text not null,
  lon          double precision not null,     -- label position
  lat          double precision not null,
  pol          int not null check (pol between 0 and 100),   -- politics
  sec          int not null check (sec between 0 and 100),   -- security
  reg          int not null check (reg between 0 and 100),   -- regulatory & resource nationalism
  log          int not null check (log between 0 and 100),   -- logistics
  san          int not null check (san between 0 and 100),   -- sanctions & compliance
  score        int generated always as (round(pol*0.20 + sec*0.25 + reg*0.25 + log*0.15 + san*0.15)) stored,
  metals       text[] not null default '{}',
  summary      text not null default '',
  watch        text[] not null default '{}',
  calendar     jsonb not null default '[]',   -- [["2027-01","Lithium export ban"], ...]
  sectors      text[] not null default '{metals}',
  updated_at   timestamptz not null default now()
);

create table if not exists assets (
  id           text primary key,              -- slug, e.g. kamoa-kakula
  name         text not null,
  iso          text not null references countries(iso),
  kind         text not null check (kind in ('mine','plant','field','refinery','lng','terminal','storage')),
  sector       text not null default 'metals' check (sector in ('metals','energy')),
  status       text not null check (status in ('op','proj','dis','plant')),  -- operating, project, disrupted, processing
  metals       text[] not null default '{}',
  operator     text not null default '',
  lon          double precision not null,
  lat          double precision not null,
  note         text not null default '',
  aliases      text[] not null default '{}', -- other names the news uses, helps matching
  updated_at   timestamptz not null default now()
);

create table if not exists ports (
  id           text primary key,
  name         text not null,
  iso          text not null references countries(iso),
  lon          double precision not null,
  lat          double precision not null,
  note         text not null default ''
);

create table if not exists corridors (
  id           text primary key,              -- e.g. lobito
  name         text not null,
  mode         text not null check (mode in ('rail','road','mixed','pipeline','sea')),
  sector       text not null default 'metals' check (sector in ('metals','energy')),
  risk         int  not null check (risk between 1 and 4),
  origin       text not null,
  destination  text not null,
  km           int  not null,
  days         text not null,
  status       text not null,
  path         jsonb not null,                -- [[lon,lat], ...]
  note         text not null default '',
  metals       text[] not null default '{}',
  serves       text[] not null default '{}'   -- asset names
);

create table if not exists chokepoints (
  id           text primary key,
  name         text not null,
  between_text text not null,
  lon          double precision not null,
  lat          double precision not null,
  risk         int not null check (risk between 1 and 4),
  note         text not null default '',
  sector       text not null default 'metals' check (sector in ('metals','energy','all'))
);

create table if not exists zones (
  id           text primary key,
  name         text not null,
  note         text not null default '',
  polygon      jsonb not null                 -- [[lon,lat], ...] (closed automatically)
);

-- ---------- Live data ----------

-- Everything the collector fetches lands here first, untouched.
create table if not exists raw_items (
  id           bigserial primary key,
  source       text not null,                 -- gdelt, reliefweb, rss, sanctions
  external_id  text,
  url          text,
  title        text not null,
  body         text not null default '',
  published_at timestamptz,
  fingerprint  text not null unique,          -- prevents duplicates
  fetched_at   timestamptz not null default now(),
  processed    boolean not null default false,
  relevant     boolean,
  event_id     bigint
);
create index if not exists raw_items_unprocessed on raw_items (processed) where processed = false;

-- Tagged, de-duplicated events. These are what the map and alerts show.
create table if not exists events (
  id           bigserial primary key,
  date         date not null,
  iso          text not null,                 -- ISO3, or XX for global
  lon          double precision not null,
  lat          double precision not null,
  type         text not null,
  severity     int not null check (severity between 1 and 4),
  metals       text[] not null default '{}',
  title        text not null,
  summary      text not null default '',
  corridors    text[] not null default '{}',
  asset_ids    text[] not null default '{}',
  status       text not null default 'pending'
               check (status in ('seed','auto','pending','approved','rejected')),
  confidence   real,
  source_urls  text[] not null default '{}',
  sector       text not null default 'metals' check (sector in ('metals','energy','all')),
  created_at   timestamptz not null default now(),
  notified_at  timestamptz
);
create index if not exists events_date on events (date desc);
create index if not exists events_status on events (status);

create table if not exists sanctions_hits (
  id           bigserial primary key,
  list_name    text not null,                 -- e.g. OFAC SDN
  entry_name   text not null,
  entry_program text not null default '',
  matched_name text not null,                 -- the operator / asset name it matched
  asset_id     text references assets(id),
  score        real not null,
  entry_type   text not null default 'entity',  -- entity | vessel | individual
  first_seen   timestamptz not null default now(),
  unique (list_name, entry_name, matched_name)
);

create table if not exists prices (
  series   text not null,
  date     date not null,
  value    numeric not null,
  unit     text not null default 'USD/bbl',
  source   text not null default 'EIA',
  fetched_at timestamptz not null default now(),
  primary key (series, date)
);

create table if not exists subscribers (
  id           bigserial primary key,
  email        text not null unique,
  countries    text[] not null default '{}',  -- empty = all
  metals       text[] not null default '{}',  -- empty = all
  corridors    text[] not null default '{}',  -- empty = all
  min_severity int not null default 3 check (min_severity between 1 and 4),
  sectors      text[] not null default '{}',   -- empty = all
  instant      boolean not null default true,
  digest       boolean not null default true,
  active       boolean not null default true,
  created_at   timestamptz not null default now()
);

create table if not exists forecasts (
  id           bigserial primary key,
  question     text not null,
  probability  int not null check (probability between 1 and 99),
  resolves_on  date not null,
  criteria     text not null,
  rationale    text not null default '',
  sector       text not null default 'metals' check (sector in ('metals','energy')),
  outcome      boolean,                        -- null until resolved
  resolved_at  date,
  created_at   timestamptz not null default now()
);

create table if not exists briefings (
  id           text primary key,              -- issue number, e.g. 000
  date         date not null,
  title        text not null,
  lede         text not null default '',
  sections     jsonb not null default '[]',   -- [["Heading","Paragraph"], ...]
  published    boolean not null default true
);

-- ---------- Security ----------
-- The website uses the public "anon" key, so it can only READ what is allowed below.
-- The collector uses the secret "service_role" key, which bypasses these rules.

alter table countries      enable row level security;
alter table assets         enable row level security;
alter table ports          enable row level security;
alter table corridors      enable row level security;
alter table chokepoints    enable row level security;
alter table zones          enable row level security;
alter table raw_items      enable row level security;
alter table events         enable row level security;
alter table sanctions_hits enable row level security;
alter table subscribers    enable row level security;
alter table forecasts      enable row level security;
alter table briefings      enable row level security;
alter table prices         enable row level security;

drop policy if exists "public read" on countries;   create policy "public read" on countries   for select to anon, authenticated using (true);
drop policy if exists "public read" on assets;      create policy "public read" on assets      for select to anon, authenticated using (true);
drop policy if exists "public read" on ports;       create policy "public read" on ports       for select to anon, authenticated using (true);
drop policy if exists "public read" on corridors;   create policy "public read" on corridors   for select to anon, authenticated using (true);
drop policy if exists "public read" on chokepoints; create policy "public read" on chokepoints for select to anon, authenticated using (true);
drop policy if exists "public read" on zones;       create policy "public read" on zones       for select to anon, authenticated using (true);
drop policy if exists "public read" on forecasts;   create policy "public read" on forecasts   for select to anon, authenticated using (true);
drop policy if exists "public read" on prices;     create policy "public read" on prices     for select to anon, authenticated using (true);
drop policy if exists "public read" on briefings;   create policy "public read" on briefings   for select to anon, authenticated using (published);
-- Only events you have approved (or low-severity ones auto-approved) are visible on the site.
drop policy if exists "public read" on events;      create policy "public read" on events      for select to anon, authenticated using (status in ('seed','auto','approved'));
-- Anyone can sign up for alerts, but nobody can read the list from the website.
drop policy if exists "signup" on subscribers;      create policy "signup" on subscribers for insert to anon, authenticated with check (active = true);
-- raw_items and sanctions_hits: no public access at all.

-- Table permissions (row level security above still applies to anon/authenticated).
grant usage on schema public to anon, authenticated, service_role;
grant select on countries, assets, ports, corridors, chokepoints, zones, events, forecasts, briefings, prices to anon, authenticated;
grant insert on subscribers to anon, authenticated;
grant all on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to anon, authenticated, service_role;

-- ---------- v3 additions: regions, oil demand, vessels (idempotent) ----------
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
