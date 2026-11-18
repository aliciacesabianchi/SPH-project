# Quartierjournal events database

Offers and events for children aged **0–4** in the **city of Zürich**, free and paid, from
Gemeinschaftszentren, Quartiertreffs and Quartierhäuser, churches, family centres, libraries,
music schools, theatres, museums and sports providers.

The YAML files here are the **source of truth**. Everything in `dist/` is generated from them.

```
data/
  providers.yaml      who runs offers (GZ Wipkingen, Ref. Kirchenkreis drei, Fadenspule, PBZ …)
  locations.yaml      where (address, Kreis, Quartier; is_place → website "places" list)
  events/*.yaml       what and when: recurring offers and dated events, grouped by provider family
  events/inbox.yaml   new entries from `qj.py add` before they are sorted into a file
  sources.yaml        pages, feeds and aggregators the daily refresh checks
  schema/schema.json  JSON Schema for all four record types
  state/              written by the daily fetch: page hashes and candidate events
```

## Records

- An **event** is an offer: either a recurring one (`schedule.kind: weekly` with `days`, `start`
  and `end`) or a dated one (`kind: dates` with a list of `dates`). Use `opening_hours` for drop-in
  places, `series` for course blocks or concert series without fixed dates yet, and `on_request`
  for offers by appointment.
- **Age** is stored in months (`min_months`, `max_months`; `null` means no upper limit), because
  "18 Monate" and "2½ Jahre" both matter for 0–4.
- Each record has a **verification** block:
  - `confirmed`: read on the provider's own page.
  - `reported`: from a search result or an aggregator.
  - `unverified`: incomplete, or from memory.

  `status: needs_verification` marks offers where a time, day or price is missing.
  `python tools/qj.py report` lists all of them.

## Adding a finding

There are three ways, from quickest to most controlled:

1. **Ask Claude**, e.g. "add this: <link>". Claude edits the YAML, runs `validate` and opens a PR.
2. **Interactive CLI**: `python tools/qj.py add` asks for the fields and writes to
   `events/inbox.yaml` (and to providers and locations if they are new).
3. **GitHub issue** with the "Neues Angebot melden" template. Parents and providers can use it
   without touching YAML. Later, the website's Submit form will write to the Supabase
   `submissions` table.

Then run:

```bash
pip install -r tools/requirements.txt
python tools/qj.py validate     # 0 errors required (CI enforces it)
python tools/qj.py build        # dist/: events.web.json, quartierjournal.sqlite, seed.sql, browser.html
python tools/qj.py report       # dist/review.md: what still needs checking
```

## Outputs (`dist/`)

| File | For |
|---|---|
| `events.web.json` | The Vue app. Events in the shape of `docs/PLAN.md` §4 (`days` 0 = Monday, decimal hours, `ageMin`/`ageMax` in years) plus `places` |
| `occurrences.json` | Weekly and dated offers expanded into concrete dates for the next 8 weeks (week planner, "today") |
| `quartierjournal.json` | Full export of all collections |
| `quartierjournal.sqlite` | Ad-hoc queries, e.g. `select * from events where is_free and age_min_months <= 12` |
| `seed.sql` | Idempotent upserts for the Supabase schema in `supabase/migrations/` |
| `browser.html` | A filterable page to browse everything |
| `review.md` | The to-do list for verification |

## Keeping it fresh

- **GitHub Action, daily at 04:17 UTC** (`.github/workflows/events-data.yml`): fetches every
  source, detects page changes, extracts schema.org/iCal/JSON events and opens a PR with
  `data/state/`. On `main` it can also push to Supabase if `SUPABASE_DB_URL` is set.
- **Daily Claude task** (`automation/daily-refresh.md`): reads the changed pages and candidates,
  adds, updates or ends records with sources, and opens a PR for review.
