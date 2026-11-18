# Handoff: Quartierjournal events database → website

Hand this file and the zip to the developer (or AI assistant) who builds the Quartierjournal website.
It explains what the database is, which file the website should read, and exactly how to wire it in.

## 1. What this is

A curated database of offers and events for **children aged 0–4 in the city of Zürich** (free and
paid): Gemeinschaftszentren, Quartiertreffs, churches, family centres (MegaMarie, Fadenspule, ELCH),
libraries (PBZ), music, theatre, museums, indoor playgrounds.

- **Source of truth:** the YAML files in `data/`. Humans and the daily Claude task edit these.
- **Generated output:** `python tools/qj.py build` writes `dist/`. The website only reads `dist/`
  (or Supabase), and never edits it.

```
data/providers.yaml          who (66)          data/locations.yaml   where (98)
data/events/*.yaml           what & when (101) data/sources.yaml     pages the daily refresh checks
data/schema/schema.json      JSON Schema       tools/qj.py           CLI: validate/build/report/add/fetch
supabase/migrations/*.sql    Postgres schema   dist/*                generated (included in the zip)
```

## 2. Which file the website uses

**`dist/events.web.json`**: `{ meta, events: WebEvent[], places: Place[] }`. It matches the data
model in `docs/PLAN.md` §4 (days with 0 = Monday, hours as decimals such as 9.5, ages in years),
with a few extra fields.

**`dist/occurrences.json`**: weekly and dated offers expanded into concrete dates for the next
8 weeks. Use it for the week planner and the "today" panel.

### TypeScript types (copy into `src/domain/types.ts`)

```ts
export type Localized = string | { de: string; en?: string; fr?: string; it?: string };
export type CategoryKey =
  | 'playground' | 'spielgruppe' | 'baby' | 'music' | 'movement' | 'museum' | 'theatre'
  | 'library' | 'church' | 'cafe' | 'course' | 'swimming' | 'nature' | 'festival' | 'advice';

export interface WebEvent {
  id: string;
  title: Localized;
  provider: string;            // display name, e.g. "GZ Seebach"
  providerId: string;
  venue: string;               // first location's name (or a free-text location note)
  kreis: number | null;        // first location's Kreis
  kreise: number[];            // all Kreise the offer happens in (multi-site offers such as MiniMove, PBZ)
  quartier: string | null;
  address: string | null;      // "Kurt-Früh-Weg 2, 8050 Zürich"
  locationIds: string[];
  scheduleKind: 'weekly' | 'dates' | 'opening_hours' | 'series' | 'on_request';
  days: number[];              // 0 = Monday … 6 = Sunday
  start: number | null;        // decimal hours, 9.5 = 09:30; null = time unknown
  end: number | null;
  dates: { date: string; start?: string; end?: string; location?: string; note?: string }[]; // ISO dates for one-off events
  season: { from?: string; to?: string; note?: string } | null;
  scheduleNote: string | null; // human text, e.g. "Mo 09:00–12:00, Do 14:30–17:30"
  categoryKey: CategoryKey;
  tags: string[];
  ageMin: number;              // years (0.5 = 6 months)
  ageMax: number | null;       // null = no upper limit
  ageMinMonths: number;
  ageMaxMonths: number | null;
  ageLabel: Localized | null;  // show this rather than computing a label
  isFree: boolean;
  price: Localized | null;     // "gratis", "CHF 5 pro Familie", "Beitrag nach eigenem Ermessen"
  priceChf: number | null;
  registration: boolean | null;  // true = required, false = none, null = recommended/unknown
  registrationRaw: 'none' | 'required' | 'recommended' | 'unknown';
  languages: string[];
  note: Localized | null;
  url: string;                 // always link out; providers change details often
  status: 'active' | 'needs_verification' | 'paused';   // 'ended' is filtered out at build time
  verification: 'confirmed' | 'reported' | 'unverified';
  checkedAt: string;           // ISO date
}

export interface Place {
  id: string; name: string; type: string; kreis: number | null; quartier: string | null;
  desc: string | null; address: { street: string | null; zip: string | null; city: string }; url: string | null;
}

export interface Occurrence {
  event: string;               // WebEvent.id
  date: string;                // ISO date
  start: string | null;        // "09:30"
  end: string | null;
  location: string | null;
  explicit: boolean;           // true = a listed date; false = generated from a weekly schedule
  check_holidays?: boolean;    // weekly offer that pauses in Zürich school holidays
}
```

## 3. Integration steps (Vue 3 + Vite + Pinia, per docs/PLAN.md)

1. Copy `dist/events.web.json` and `dist/occurrences.json` to `public/data/`. Alternatively,
   import them in `src/data/events.ts`, which puts them in the bundle.
2. Load them in `src/services/api.ts` behind the existing interface:
   ```ts
   export async function getEvents(): Promise<{ events: WebEvent[]; places: Place[] }> {
     const r = await fetch('/data/events.web.json'); return r.json();
   }
   export async function getOccurrences(): Promise<Occurrence[]> {
     const r = await fetch('/data/occurrences.json'); return r.json();
   }
   ```
   Later, swap the body of these functions for Supabase (§5) without touching any components.
3. **Discover filters** (pure functions in `src/domain/filters.ts`):
   - **Age:** the child's age in months `m` matches when
     `ageMinMonths <= m && (ageMaxMonths == null || ageMaxMonths >= m)`.
   - **Kreis:** match on `kreise` (an array), not only `kreis`.
   - **Weekday:** match on `days`, or on the weekday of any entry in `dates`.
   - **Gratis:** `isFree`. **Ohne Anmeldung:** `registrationRaw === 'none'`.
   - **Time of day:** use `start`. If it is `null`, keep the event and show `scheduleNote`.
4. **Event card:**
   - Show `title` (by locale), `provider` · `venue` · Kreis, the schedule, `ageLabel`, the price
     and the registration status.
   - Schedule: render `days` and `start`–`end`. When `dates` is non-empty, show the next dates
     instead. If both are empty, fall back to `scheduleNote`.
   - Always show a "Zur Website" link to `url`.
   - Show a small trust badge: `confirmed` = "bestätigt", `reported` = "gemeldet",
     `unverified` = "zu prüfen".
   - When `status === 'needs_verification'`, show "Details beim Anbieter prüfen".
5. **Sorting:** confirmed before reported before unverified. Put
   `status === 'needs_verification'` last.
6. **Week planner / "fits my care gaps":** use `occurrences.json`. When `check_holidays` is true,
   hide the occurrence during Zürich school holidays. Keep a small table of holiday dates in
   `src/domain/time.ts`.
7. **Places list:** use `places` from `events.web.json`.
8. **Submit-event form:** in the frontend-only phase, link to the GitHub issue template
   `.github/ISSUE_TEMPLATE/neues-angebot.yml`. With Supabase, insert into `submissions` (§5).

### Display rules

- Every offer depends on the provider, and times change, so never present an offer as guaranteed.
  Keep the outbound link visible.
- Weekly offers usually pause in school holidays. Show that hint wherever `check_holidays`
  is true.
- Titles may be a plain string or a `{de, en}` object. Use one helper:
  `const t = (v, lang) => typeof v === 'string' ? v : (v?.[lang] ?? v?.de ?? '')`.

## 4. Keeping the data fresh (no website code needed)

- `.github/workflows/events-data.yml` validates and builds on every change. Each night it fetches
  the sources and opens a pull request. When `SUPABASE_DB_URL` is set, merging to `main` pushes
  the data to Supabase.
- `automation/daily-refresh.md` holds the prompt for a daily Claude task. It updates the YAML and
  opens a pull request for review.
- After each merge, rebuild (`python tools/qj.py build`) and redeploy the site, or read from
  Supabase so no redeploy is needed.

## 5. Optional: read from Supabase instead of JSON

1. Run `supabase/migrations/20261118000000_events_db.sql`, then `dist/seed.sql`.
2. Read with:
   ```ts
   const { data } = await supabase.from('events_public').select('*');
   ```
   - Each row has: `title` (jsonb), `schedule` (jsonb with `days` as `'mon'…'sun'`, `start` and
     `end` as `"HH:MM"`), `age_min_months`, `age_max_months`, `is_free`, `registration`, `venue`,
     `kreis`, `quartier`, `address`, `provider_name`.
   - To get the same shape as `WebEvent`, convert days to 0–6 and times to decimal hours in
     `api.ts`.
3. Submit form:
   ```ts
   await supabase.from('submissions').insert({ title, provider_name, venue, when_text, age_text, price_text, url })
   ```
   RLS lets anyone insert into `submissions` and read the directory, and nothing else.

## 6. Commands

```bash
pip install -r tools/requirements.txt
python tools/qj.py validate   # must show 0 errors
python tools/qj.py build      # regenerates dist/
python tools/qj.py report     # dist/review.md – what still needs checking
python tools/qj.py add        # add an offer interactively
```
