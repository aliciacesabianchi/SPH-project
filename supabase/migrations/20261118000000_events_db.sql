-- Quartierjournal events database (Supabase / Postgres).
-- Source of truth stays in data/*.yaml; `python tools/qj.py build` writes dist/seed.sql with idempotent upserts.
-- The website reads the public views; parents/providers write only to `submissions` (moderated).

create table if not exists providers (
  id text primary key,
  name text not null,
  type text not null,
  parent_id text references providers(id),
  website text, events_url text, email text, phone text,
  description jsonb,                     -- string or {de,en,fr,it}
  verification jsonb not null,           -- {status, checked_at, method, evidence, note}
  updated_at timestamptz not null default now()
);

create table if not exists locations (
  id text primary key,
  name text not null,
  kind text not null,
  street text, zip text, city text not null default 'Zürich',
  kreis smallint check (kreis between 1 and 12),
  quartier text,
  lat double precision, lng double precision,
  is_place boolean not null default false,
  stroller_friendly boolean,
  url text, note text,
  verification jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists provider_locations (
  provider_id text references providers(id) on delete cascade,
  location_id text references locations(id) on delete cascade,
  primary key (provider_id, location_id)
);

create table if not exists events (
  id text primary key,
  title jsonb not null,                  -- {de, en, ...}
  provider_id text not null references providers(id),
  category text not null,
  tags jsonb not null default '[]',
  age_min_months smallint not null,
  age_max_months smallint,               -- null = no upper limit
  age_label jsonb,
  schedule jsonb not null,               -- {kind, days, start, end, season, pauses, dates[], note}
  is_free boolean not null,
  price_chf numeric(8,2),
  price_text jsonb,
  registration text not null check (registration in ('none','required','recommended','unknown')),
  languages jsonb not null default '[]',
  description jsonb,
  url text,
  location_note text,
  status text not null check (status in ('active','needs_verification','paused','ended')),
  verification jsonb not null,
  updated_at timestamptz not null default now()
);
create index if not exists events_age_idx on events (age_min_months, age_max_months);
create index if not exists events_status_idx on events (status);

create table if not exists event_locations (
  event_id text references events(id) on delete cascade,
  location_id text references locations(id) on delete cascade,
  primary key (event_id, location_id)
);

create table if not exists sources (
  id text primary key,
  name text not null, url text not null, kind text not null,
  provider_id text references providers(id),
  method text not null, priority text not null,
  covers text, note text,
  last_checked_at timestamptz, last_hash text, last_error text
);

-- New findings from the website's "Angebot melden" form, the daily Claude task or a scraper.
-- A human (or the Claude task, via PR) turns approved rows into YAML records.
create table if not exists submissions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  origin text not null default 'website' check (origin in ('website','claude','scraper','email')),
  title text not null,
  provider_name text, venue text, address text, kreis smallint,
  when_text text,                         -- free text: "jeden Mittwoch 9:30–11", "12.12.2026 15 Uhr"
  age_text text, price_text text, url text,
  submitter_email text,
  payload jsonb,                          -- raw structured data (JSON-LD, scraper output)
  status text not null default 'new' check (status in ('new','accepted','duplicate','rejected')),
  review_note text
);

-- Website-facing view: one row per event with its first location flattened.
create or replace view events_public as
select e.*, l.name as venue, l.kreis, l.quartier,
       concat_ws(', ', l.street, concat_ws(' ', l.zip, l.city)) as address,
       p.name as provider_name
from events e
join providers p on p.id = e.provider_id
left join lateral (
  select loc.* from event_locations el join locations loc on loc.id = el.location_id
  where el.event_id = e.id order by loc.id limit 1
) l on true
where e.status <> 'ended';

-- Row level security: everyone may read the directory; only anonymous *inserts* into submissions.
alter table providers enable row level security;
alter table locations enable row level security;
alter table provider_locations enable row level security;
alter table events enable row level security;
alter table event_locations enable row level security;
alter table sources enable row level security;
alter table submissions enable row level security;

do $$ begin
  create policy "public read" on providers for select using (true);
  create policy "public read" on locations for select using (true);
  create policy "public read" on provider_locations for select using (true);
  create policy "public read" on events for select using (true);
  create policy "public read" on event_locations for select using (true);
  create policy "anyone can submit" on submissions for insert with check (status = 'new');
exception when duplicate_object then null; end $$;
