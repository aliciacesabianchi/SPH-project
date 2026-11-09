# Quartierjournal: implementation plan

Status: **draft for review**. No app code exists yet.
Source: `Quartierjournal-standalone.html`, a bundled prototype that runs as one ~200 KB component.

## 1. What the prototype does

Quartierjournal helps families with small children in Zürich find activities near them and fit them into the week.

| Area | Prototype behaviour |
|---|---|
| **Home** (`home`) | Hero carousel, "how it works" steps, an event carousel tailored to the kid's age and Kreis, a demo week planner, and a call to sign up |
| **Discover** (`discover`) | Event list with filters: category, Kreis (multi-select), age, time of day, gratis, no registration, favourites only, and "fits my care gaps". A text search, paging (12 at a time) and a static list of "places" (parks, museums, Badis) |
| **Week** (`week`) | Care schedule blocks per caregiver (me, Kita, Nonna, Papa), drag to resize, and events placed into free slots. Week, month and year views. Join or leave, recurring events, "today" panel, and export to Google Calendar and .ics |
| **Friends** | Invite friends (WhatsApp, Telegram, email, copy link), see which events friends join or are invited to, and set event visibility (private or public) |
| **Profile** (`profile`) | Parent name, email, phone and Kreis. Kids (name, age), team members (partner and other caregivers), avatars (photo, illustrated, or built-in "fantasy" SVGs), and notification settings |
| **Auth** (`signup`) | Login and signup (name, email, password, Kreis, kid name and age). This is mocked and the state is `loggedIn` |
| **Submit event** | A form where providers or parents suggest an event (title, place, day, time, offer) |
| **Static** (`about`, `how`, `contact`, `public`) | About, explainer, a contact form with categories, and a public shared-plan page |
| **i18n** | DE (default), EN, IT and FR. The language is saved in `localStorage` |

The seed data has about 17 recurring weekly events (title, venue, Kreis, Quartier, weekdays, start and end time, category, age label and range, price, registration, note, address, URL) and 18 places.
The design uses DM Sans on a warm off-white background, with ink `#1d1d1b`, yellow `#FFD54F`, magenta `#D2357F`, green `#4DBB6F`, purple `#8E6BAA` and blue `#5DCBE8`.

## 2. Target stack

- **Vue 3 + Vite + TypeScript**, using `<script setup>` single-file components
- **Vue Router**: one route per prototype view, so URLs can be shared (`/discover?kreis=6&age=2`)
- **Pinia** for state, split into stores (below), with local storage for the frontend-only phase
- **vue-i18n**: four locale JSON files, extracted from the inline `{ de, en, it, fr }` objects
- **Styling**: plain CSS with design tokens as CSS custom properties (`tokens.css`) and scoped component styles. No CSS framework, because the prototype's look is bespoke
- **Quality**: ESLint, Prettier, `vue-tsc`, Vitest (unit tests for the date, slot and filter logic), Playwright (a few end-to-end smoke tests)
- **Package manager**: pnpm

## 3. Project structure

```
src/
  main.ts, App.vue, router.ts
  styles/        tokens.css, base.css
  i18n/          index.ts, de.json, en.json, fr.json, it.json
  domain/        types.ts           Event, Place, CareBlock, Kid, Friend, Invite…
                 time.ts            monday(), dateOf(), hm(), week offsets
                 schedule.ts        free-slot calculation, "fits my care gaps"
                 filters.ts         discover filtering and sorting (pure functions)
                 ics.ts             .ics and Google Calendar link builders
  data/          events.ts, places.ts   seed data (typed, from the prototype)
  services/      api.ts             data-access interface (local now, HTTP later)
  stores/        session, profile, events, schedule, social, ui
  components/
    layout/      AppHeader, LangSwitcher, CitySwitcher, AppFooter, Toast
    events/      EventCard, EventCarousel, EventDetail, FilterBar, KreisPicker
    schedule/    WeekGrid, CareBlock, MonthView, YearView, TodayPanel
    social/      FriendList, InviteSheet, ShareButtons
    profile/     KidEditor, TeamEditor, AvatarPicker, FantasyAvatar
    common/      Chip, Dropdown, Modal, Button
  views/         HomeView, DiscoverView, WeekView, FriendsView, ProfileView,
                 AuthView, SubmitEventView, AboutView, HowView, ContactView,
                 PublicPlanView
```

The key principle is to move the logic out of the giant component into **pure, tested functions** in `domain/`. Components only render and dispatch.

## 4. Data model (TypeScript)

```ts
type Weekday = 0 | 1 | 2 | 3 | 4 | 5 | 6;       // 0 = Monday, as in the prototype
type CategoryKey = 'playground' | 'spielgruppe' | 'baby' | 'music' | 'movement' | 'museum';

interface Event {
  id: string; title: LocalizedText; venue: string; kreis: number; quartier: string;
  days: Weekday[]; start: number; end: number;      // decimal hours, e.g. 9.75
  category: string; categoryKey: CategoryKey;
  ageLabel: LocalizedText; ageMin: number; ageMax: number;
  price: LocalizedText; isFree: boolean; registration: boolean | null;
  note?: LocalizedText; address?: string; url: string; season?: DateRange;
}
interface Place  { id; name; type; kreis; quartier; desc: LocalizedText; address?; url }
interface Caregiver { id: string; name: string; color: string }   // me, kita, oma, papa…
interface CareBlock { id: string; day: Weekday; start: number; end: number; who: string }
interface Kid    { name: string; age: number; avatar?: string }
interface Plan   { eventId: string; date?: string; recurring: boolean; visibility: 'private'|'public' }
interface Friend { name: string; avatar?: string }
interface Invite { eventId: string; friend: string; status: 'invited'|'joined' }
```

`LocalizedText` is `string | Partial<Record<Locale, string>>`. Seed data is German-first, as in the prototype.

## 5. Delivery phases

**Phase 0: scaffold (about half a day)**
Create the Vite, TS, router, Pinia, i18n, lint, test and CI setup (GitHub Actions: lint, type-check, unit tests). Add the tokens, base layout and the header with the language switcher.

**Phase 1: domain and data (about 1 day)**
Port the seed events and places into typed files. Write and test `time.ts`, `filters.ts`, `schedule.ts` and `ics.ts`, checking them against prototype behaviour.

**Phase 2: Discover (1–2 days)**
Build EventCard, FilterBar (all filters, synced to the URL query), favourites, paging, places list and event detail.

**Phase 3: Home (about 1 day)**
Build the hero carousel, how-it-works section, personalised carousel and the demo planner (read-only WeekGrid).

**Phase 4: Week planner (2–3 days)**
Build WeekGrid with drag-resizable care blocks, events in free slots, join or leave, recurring plans, month and year views, the today panel, and .ics and Google Calendar export.

**Phase 5: Profile and auth, mocked (1 day)**
Add kids, team, avatars (port `fantasyAvatar` into a component) and settings. Login and signup run against a local mock, behind the `services/api.ts` interface.

**Phase 6: Friends and sharing (1 day)**
Add invites, share links, friend status per event, visibility, and the public plan page.

**Phase 7: static pages and polish (1 day)**
Add About, How, Contact and Submit-event. Then mobile checks, accessibility (keyboard, focus, contrast, aria) and Playwright smoke tests.

That comes to roughly **8–11 working days** for a frontend that matches the prototype, with persistence in the browser.

## 6. Later: backend (out of scope for now)

The `services/api.ts` boundary lets a backend be added later without touching components. Likely needs:

- Auth (email and password or magic link)
- Database tables for users, kids, care blocks, plans and invites
- An events and places database with an admin or moderation flow for submitted events
- Real invite links and notifications

Possible options are Supabase, or a small Node API with Postgres. That decision can wait until the frontend is validated.

## 7. Open questions for you

1. **Fidelity**: should the result be a pixel-faithful port, or is cleaning up the UI while porting acceptable?
2. **Cities**: the prototype has a `city` switch (`zh`). Is Zürich the only city for v1?
3. **Image assets**: the bundle references `assets/avatar-*.png` and provider images. Can you add the originals to the repo, or should placeholders be used?
4. **Event data**: will events stay a curated static list for now, or should a data source or scraper be planned?
5. **Hosting**: Netlify, Vercel, GitHub Pages, or something else?
6. **Prototype file**: should the standalone HTML be committed under `prototype/` for reference?
