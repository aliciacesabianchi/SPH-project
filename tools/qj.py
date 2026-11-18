#!/usr/bin/env python3
"""Quartierjournal events database CLI.

    python tools/qj.py validate            # schema + cross-reference checks (CI runs this)
    python tools/qj.py build               # dist/: JSON for the website, SQLite, Postgres seed, HTML browser
    python tools/qj.py report              # dist/review.md: what needs verification, stale items, coverage
    python tools/qj.py add                 # interactive: add an event (and provider/location if new)
    python tools/qj.py fetch [--source ID] # daily: fetch sources, detect changes, extract JSON-LD/iCal events
    python tools/qj.py occurrences --weeks 8

The YAML files in data/ are the source of truth. Everything in dist/ is generated.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

try:
    import jsonschema
except ImportError:  # validation still does cross-reference checks without it
    jsonschema = None

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DIST = ROOT / "dist"
STATE = DATA / "state"
TODAY = dt.date.today()
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
COLLECTIONS = {"providers": "provider", "locations": "location", "events": "event", "sources": "source"}


# ----------------------------------------------------------------------------- loading
class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps dates as ISO strings (JSON-schema and JSON friendly)."""


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _read(path: Path) -> list[dict]:
    doc = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader) or {}
    defaults = doc.get("defaults", {}) or {}
    items = []
    for item in doc.get("items", []) or []:
        merged = {k: v for k, v in defaults.items() if k not in item}
        merged.update(item)
        merged["_file"] = str(path.relative_to(ROOT))
        items.append(merged)
    return items


def load() -> dict[str, list[dict]]:
    db = {
        "providers": _read(DATA / "providers.yaml"),
        "locations": _read(DATA / "locations.yaml"),
        "sources": _read(DATA / "sources.yaml"),
        "events": [],
    }
    for f in sorted((DATA / "events").glob("*.yaml")):
        db["events"].extend(_read(f))
    return db


def clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def text(value, lang="de") -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return value.get(lang) or value.get("de") or next(iter(value.values()), "")
    return str(value)


# ----------------------------------------------------------------------------- validation
def validate(db, quiet=False) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    schema = json.loads((DATA / "schema" / "schema.json").read_text(encoding="utf-8"))
    ids = {c: {} for c in COLLECTIONS}

    for coll, key in COLLECTIONS.items():
        sub = {"$defs": schema["$defs"], **schema[key]}
        validator = jsonschema.Draft202012Validator(sub) if jsonschema else None
        for item in db[coll]:
            where = f"{item['_file']}:{item.get('id', '?')}"
            if validator:
                for e in validator.iter_errors(clean(item)):
                    path = "/".join(str(p) for p in e.absolute_path)
                    errors.append(f"{where}: {path or '(root)'}: {e.message}")
            iid = item.get("id")
            if iid in ids[coll]:
                errors.append(f"{where}: duplicate {key} id (also in {ids[coll][iid]['_file']})")
            ids[coll][iid] = item

    P, L = ids["providers"], ids["locations"]
    for p in db["providers"]:
        where = f"{p['_file']}:{p['id']}"
        if p.get("parent") and p["parent"] not in P:
            errors.append(f"{where}: unknown parent provider '{p['parent']}'")
        for loc in p.get("locations", []):
            if loc not in L:
                errors.append(f"{where}: unknown location '{loc}'")
    for s in db["sources"]:
        if s.get("provider") and s["provider"] not in P:
            errors.append(f"{s['_file']}:{s['id']}: unknown provider '{s['provider']}'")

    for e in db["events"]:
        where = f"{e['_file']}:{e['id']}"
        if e.get("provider") not in P:
            errors.append(f"{where}: unknown provider '{e.get('provider')}'")
        for loc in e.get("locations", []) or []:
            if loc not in L:
                errors.append(f"{where}: unknown location '{loc}'")
        if not e.get("locations") and not e.get("location_note"):
            warnings.append(f"{where}: no location and no location_note")
        age = e.get("age", {})
        if age.get("max_months") is not None and age["max_months"] < age.get("min_months", 0):
            errors.append(f"{where}: age.max_months < age.min_months")
        if age.get("min_months", 0) > 48:
            warnings.append(f"{where}: min age {age['min_months']} months is above the 0–4 target")
        sch = e.get("schedule", {})
        if sch.get("start") and sch.get("end") and sch["end"] <= sch["start"]:
            errors.append(f"{where}: schedule.end must be after start")
        if sch.get("kind") == "dates" and not sch.get("dates"):
            errors.append(f"{where}: schedule.kind=dates but no dates")
        for d in sch.get("dates", []) or []:
            if d.get("location") and d["location"] not in L:
                errors.append(f"{where}: unknown date location '{d['location']}'")
        if sch.get("kind") == "dates" and e.get("status") == "active":
            if all(d["date"] < TODAY.isoformat() for d in sch.get("dates", [])):
                warnings.append(f"{where}: all dates are in the past → set status: ended")
        to = (sch.get("season") or {}).get("to")
        if to and to < TODAY.isoformat() and e.get("status") == "active":
            warnings.append(f"{where}: season ended {to} → set status: ended or update season")
        checked = (e.get("verification") or {}).get("checked_at")
        if checked and (TODAY - dt.date.fromisoformat(checked)).days > 60:
            warnings.append(f"{where}: last checked {checked} (>60 days)")

    if not quiet:
        for w in warnings:
            print(f"warning: {w}")
        for e in errors:
            print(f"ERROR:   {e}")
        c = {k: len(v) for k, v in db.items()}
        print(f"\n{c['providers']} providers, {c['locations']} locations, {c['events']} events, "
              f"{c['sources']} sources — {len(errors)} errors, {len(warnings)} warnings")
    return errors, warnings


# ----------------------------------------------------------------------------- outputs
def _hours(t: str | None):
    if not t:
        return None
    h, m = t.split(":")
    return int(h) + int(m) / 60


def web_event(e, P, L) -> dict:
    """Flatten an event into the shape the Quartierjournal Vue app uses (docs/PLAN.md §4)."""
    locs = [L[x] for x in e.get("locations", []) or [] if x in L]
    first = locs[0] if locs else {}
    addr = first.get("address", {})
    sch = e["schedule"]
    age = e["age"]
    return {
        "id": e["id"],
        "title": e["title"],
        "provider": P[e["provider"]]["name"],
        "providerId": e["provider"],
        "venue": first.get("name") or e.get("location_note", ""),
        "kreis": first.get("kreis"),
        "kreise": sorted({l["kreis"] for l in locs if l.get("kreis")}),
        "quartier": first.get("quartier"),
        "address": ", ".join(x for x in [addr.get("street"), " ".join(filter(None, [addr.get("zip"), addr.get("city")]))] if x) or None,
        "locationIds": [l["id"] for l in locs],
        "scheduleKind": sch["kind"],
        "days": [WEEKDAYS.index(d) for d in sch.get("days", [])],  # 0 = Monday, as in the prototype
        "start": _hours(sch.get("start")),
        "end": _hours(sch.get("end")),
        "dates": sch.get("dates", []),
        "season": sch.get("season"),
        "scheduleNote": sch.get("note"),
        "categoryKey": e["category"],
        "tags": e.get("tags", []),
        "ageMin": round(age["min_months"] / 12, 2),
        "ageMax": None if age.get("max_months") is None else round(age["max_months"] / 12, 2),
        "ageMinMonths": age["min_months"],
        "ageMaxMonths": age.get("max_months"),
        "ageLabel": age.get("label"),
        "isFree": e["price"]["free"],
        "price": e["price"].get("text") or ("gratis" if e["price"]["free"] else None),
        "priceChf": e["price"].get("amount_chf"),
        "registration": {"none": False, "required": True, "recommended": None, "unknown": None}[e["registration"]],
        "registrationRaw": e["registration"],
        "languages": e.get("languages", []),
        "note": e.get("description"),
        "url": e.get("url") or P[e["provider"]].get("website"),
        "status": e["status"],
        "verification": e["verification"]["status"],
        "checkedAt": e["verification"]["checked_at"],
    }


def occurrences(db, weeks=8, start=None) -> list[dict]:
    """Expand weekly and dated events into concrete dated occurrences."""
    start = start or TODAY
    end = start + dt.timedelta(weeks=weeks)
    out = []
    for e in db["events"]:
        if e["status"] in ("ended", "paused"):
            continue
        sch = e["schedule"]
        season = sch.get("season") or {}
        s_from = dt.date.fromisoformat(season["from"]) if season.get("from") else None
        s_to = dt.date.fromisoformat(season["to"]) if season.get("to") else None
        for d in sch.get("dates", []) or []:
            day = dt.date.fromisoformat(d["date"])
            if start <= day < end:
                out.append({"event": e["id"], "date": d["date"], "start": d.get("start") or sch.get("start"),
                            "end": d.get("end") or sch.get("end"), "location": d.get("location"), "explicit": True})
        if sch["kind"] in ("weekly", "opening_hours") and sch.get("days"):
            day = start
            while day < end:
                if WEEKDAYS[day.weekday()] in sch["days"] and (not s_from or day >= s_from) and (not s_to or day <= s_to):
                    if not any(o["event"] == e["id"] and o["date"] == day.isoformat() for o in out):
                        out.append({"event": e["id"], "date": day.isoformat(), "start": sch.get("start"),
                                    "end": sch.get("end"), "location": None, "explicit": False,
                                    "check_holidays": sch.get("pauses") == "school_holidays"})
                day += dt.timedelta(days=1)
    return sorted(out, key=lambda o: (o["date"], o["start"] or "99"))


SQL_SCHEMA = (ROOT / "supabase" / "migrations").glob("*_events_db.sql")


def build_sqlite(db, path: Path):
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE providers (id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT, parent_id TEXT, website TEXT,
      events_url TEXT, email TEXT, phone TEXT, description TEXT, verification TEXT, checked_at TEXT);
    CREATE TABLE locations (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT, street TEXT, zip TEXT, city TEXT,
      kreis INTEGER, quartier TEXT, lat REAL, lng REAL, is_place INTEGER, stroller_friendly INTEGER, url TEXT,
      note TEXT, verification TEXT, checked_at TEXT);
    CREATE TABLE provider_locations (provider_id TEXT, location_id TEXT, PRIMARY KEY (provider_id, location_id));
    CREATE TABLE events (id TEXT PRIMARY KEY, title_de TEXT, title_en TEXT, provider_id TEXT REFERENCES providers(id),
      category TEXT, tags TEXT, age_min_months INTEGER, age_max_months INTEGER, age_label TEXT,
      schedule_kind TEXT, days TEXT, start_time TEXT, end_time TEXT, frequency TEXT, season_from TEXT, season_to TEXT,
      pauses TEXT, schedule_note TEXT, is_free INTEGER, price_chf REAL, price_text TEXT, registration TEXT,
      languages TEXT, description TEXT, url TEXT, location_note TEXT, status TEXT, verification TEXT,
      checked_at TEXT, evidence TEXT, raw_json TEXT);
    CREATE TABLE event_locations (event_id TEXT, location_id TEXT, PRIMARY KEY (event_id, location_id));
    CREATE TABLE event_dates (event_id TEXT, date TEXT, start_time TEXT, end_time TEXT, location_id TEXT, note TEXT);
    CREATE TABLE sources (id TEXT PRIMARY KEY, name TEXT, url TEXT, kind TEXT, provider_id TEXT, method TEXT,
      priority TEXT, covers TEXT, note TEXT);
    CREATE TABLE occurrences (event_id TEXT, date TEXT, start_time TEXT, end_time TEXT, location_id TEXT, explicit INTEGER);
    """)
    for p in db["providers"]:
        v = p["verification"]
        con.execute("INSERT INTO providers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (p["id"], p["name"], p["type"], p.get("parent"), p.get("website"), p.get("events_url"),
                     p.get("email"), p.get("phone"), text(p.get("description")), v["status"], v["checked_at"]))
        for loc in p.get("locations", []):
            con.execute("INSERT OR IGNORE INTO provider_locations VALUES (?,?)", (p["id"], loc))
    for l in db["locations"]:
        a, g, v = l["address"], l.get("geo") or {}, l["verification"]
        con.execute("INSERT INTO locations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (l["id"], l["name"], l["kind"], a.get("street"), a.get("zip"), a.get("city"), l.get("kreis"),
                     l.get("quartier"), g.get("lat"), g.get("lng"), int(bool(l.get("is_place"))),
                     l.get("stroller_friendly"), l.get("url"), l.get("note"), v["status"], v["checked_at"]))
    for e in db["events"]:
        s, a, pr, v = e["schedule"], e["age"], e["price"], e["verification"]
        season = s.get("season") or {}
        con.execute("INSERT INTO events VALUES (" + ",".join("?" * 31) + ")",
                    (e["id"], text(e["title"]), text(e["title"], "en"), e["provider"], e["category"],
                     json.dumps(e.get("tags", [])), a["min_months"], a.get("max_months"), text(a.get("label")),
                     s["kind"], json.dumps(s.get("days", [])), s.get("start"), s.get("end"), s.get("frequency"),
                     season.get("from"), season.get("to"), s.get("pauses"), s.get("note"), int(pr["free"]),
                     pr.get("amount_chf"), text(pr.get("text")), e["registration"], json.dumps(e.get("languages", [])),
                     text(e.get("description")), e.get("url"), e.get("location_note"), e["status"], v["status"],
                     v["checked_at"], v.get("evidence"), json.dumps(clean(e), ensure_ascii=False)))
        for loc in e.get("locations", []) or []:
            con.execute("INSERT OR IGNORE INTO event_locations VALUES (?,?)", (e["id"], loc))
        for d in s.get("dates", []) or []:
            con.execute("INSERT INTO event_dates VALUES (?,?,?,?,?,?)",
                        (e["id"], d["date"], d.get("start"), d.get("end"), d.get("location"), d.get("note")))
    for s in db["sources"]:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?)",
                    (s["id"], s["name"], s["url"], s["kind"], s.get("provider"), s["method"], s["priority"],
                     s.get("covers"), s.get("note")))
    for o in occurrences(db):
        con.execute("INSERT INTO occurrences VALUES (?,?,?,?,?,?)",
                    (o["event"], o["date"], o["start"], o["end"], o["location"], int(o["explicit"])))
    con.commit()
    con.close()


def _sql(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return "'" + json.dumps(v, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def build_seed_sql(db, path: Path):
    """Idempotent upserts for the Supabase/Postgres schema in supabase/migrations/."""
    lines = ["-- Generated by tools/qj.py build. Do not edit; edit data/*.yaml instead.", "begin;"]

    def upsert(table, row: dict):
        cols = ", ".join(row)
        vals = ", ".join(_sql(v) for v in row.values())
        upd = ", ".join(f"{c} = excluded.{c}" for c in row if c != "id")
        lines.append(f"insert into {table} ({cols}) values ({vals}) on conflict (id) do update set {upd};")

    for p in sorted(db["providers"], key=lambda p: bool(p.get("parent"))):  # parents first
        upsert("providers", {"id": p["id"], "name": p["name"], "type": p["type"], "parent_id": p.get("parent"),
                             "website": p.get("website"), "events_url": p.get("events_url"), "email": p.get("email"),
                             "phone": p.get("phone"), "description": p.get("description"),
                             "verification": p["verification"]})
    for l in db["locations"]:
        a, g = l["address"], l.get("geo") or {}
        upsert("locations", {"id": l["id"], "name": l["name"], "kind": l["kind"], "street": a.get("street"),
                             "zip": a.get("zip"), "city": a.get("city"), "kreis": l.get("kreis"),
                             "quartier": l.get("quartier"), "lat": g.get("lat"), "lng": g.get("lng"),
                             "is_place": bool(l.get("is_place")), "stroller_friendly": l.get("stroller_friendly"),
                             "url": l.get("url"), "note": l.get("note"), "verification": l["verification"]})
    lines.append("delete from provider_locations;")
    for p in db["providers"]:
        for loc in p.get("locations", []):
            lines.append(f"insert into provider_locations values ({_sql(p['id'])}, {_sql(loc)}) on conflict do nothing;")
    for e in db["events"]:
        upsert("events", {"id": e["id"], "title": e["title"] if isinstance(e["title"], dict) else {"de": e["title"]},
                          "provider_id": e["provider"], "category": e["category"], "tags": e.get("tags", []),
                          "age_min_months": e["age"]["min_months"], "age_max_months": e["age"].get("max_months"),
                          "age_label": e["age"].get("label"), "schedule": e["schedule"], "is_free": e["price"]["free"],
                          "price_chf": e["price"].get("amount_chf"), "price_text": e["price"].get("text"),
                          "registration": e["registration"], "languages": e.get("languages", []),
                          "description": e.get("description"), "url": e.get("url"),
                          "location_note": e.get("location_note"), "status": e["status"],
                          "verification": e["verification"]})
    lines.append("delete from event_locations;")
    for e in db["events"]:
        for loc in e.get("locations", []) or []:
            lines.append(f"insert into event_locations values ({_sql(e['id'])}, {_sql(loc)}) on conflict do nothing;")
    for s in db["sources"]:
        upsert("sources", {"id": s["id"], "name": s["name"], "url": s["url"], "kind": s["kind"],
                           "provider_id": s.get("provider"), "method": s["method"], "priority": s["priority"],
                           "covers": s.get("covers"), "note": s.get("note")})
    lines.append("commit;")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_browser(payload: dict, path: Path):
    tpl = (ROOT / "tools" / "browser_template.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(tpl.replace("/*__DATA__*/null", data), encoding="utf-8")


def cmd_build(args):
    db = load()
    errors, _ = validate(db, quiet=True)
    if errors:
        print("\n".join(errors))
        sys.exit("validation failed; nothing built")
    DIST.mkdir(exist_ok=True)
    P = {p["id"]: p for p in db["providers"]}
    L = {l["id"]: l for l in db["locations"]}
    web = [web_event(e, P, L) for e in db["events"] if e["status"] != "ended"]
    places = [{"id": l["id"], "name": l["name"], "type": l["kind"], "kreis": l.get("kreis"),
               "quartier": l.get("quartier"), "desc": l.get("note"), "address": l["address"], "url": l.get("url")}
              for l in db["locations"] if l.get("is_place")]
    meta = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "counts": {k: len(v) for k, v in db.items()}}
    full = {"meta": meta, **{k: [clean(i) for i in v] for k, v in db.items()}}
    (DIST / "quartierjournal.json").write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
    (DIST / "events.web.json").write_text(json.dumps({"meta": meta, "events": web, "places": places},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")
    occ = occurrences(db, weeks=args.weeks)
    (DIST / "occurrences.json").write_text(json.dumps(occ, ensure_ascii=False, indent=1), encoding="utf-8")
    build_sqlite(db, DIST / "quartierjournal.sqlite")
    build_seed_sql(db, DIST / "seed.sql")
    build_browser({"meta": meta, "events": web, "providers": full["providers"], "locations": full["locations"],
                   "sources": full["sources"]}, DIST / "browser.html")
    print(f"built dist/: {len(web)} web events, {len(places)} places, {len(occ)} occurrences "
          f"(next {args.weeks} weeks), sqlite, seed.sql, browser.html")


# ----------------------------------------------------------------------------- report
def cmd_report(args):
    db = load()
    errors, warnings = validate(db, quiet=True)
    P = {p["id"]: p for p in db["providers"]}
    out = [f"# Database review – {TODAY.isoformat()}", ""]
    c = Counter(e["verification"]["status"] for e in db["events"])
    out += [f"- **{len(db['events'])} events** from {len(db['providers'])} providers at {len(db['locations'])} locations",
            f"- verification: {dict(c)}",
            f"- free: {sum(e['price']['free'] for e in db['events'])} / paid: {sum(not e['price']['free'] for e in db['events'])}",
            f"- {len(errors)} errors, {len(warnings)} warnings", ""]
    kreis = Counter()
    L = {l["id"]: l for l in db["locations"]}
    for e in db["events"]:
        for k in {L[x]["kreis"] for x in e.get("locations", []) if L.get(x, {}).get("kreis")}:
            kreis[k] += 1
    out += ["## Events per Kreis", "", "| Kreis | events |", "|---|---|"]
    out += [f"| {k} | {kreis.get(k, 0)} |" for k in range(1, 13)] + [""]
    todo = [e for e in db["events"] if e["status"] == "needs_verification" or e["verification"]["status"] == "unverified"]
    out += [f"## Events to verify ({len(todo)})", ""]
    by = defaultdict(list)
    for e in todo:
        by[e["provider"]].append(e)
    for pid, evs in sorted(by.items()):
        out.append(f"**{P[pid]['name']}**")
        for e in evs:
            out.append(f"- `{e['id']}` {text(e['title'])} – {e['schedule'].get('note') or e['verification'].get('note') or ''} ({e.get('url', '')})")
        out.append("")
    unv = [l for l in db["locations"] if l["verification"]["status"] == "unverified"]
    out += [f"## Locations with unverified/missing address ({len(unv)})", ""]
    out += [f"- `{l['id']}` {l['name']}: {l['verification'].get('note', '')}" for l in unv] + [""]
    if warnings:
        out += ["## Warnings", ""] + [f"- {w}" for w in warnings]
    DIST.mkdir(exist_ok=True)
    (DIST / "review.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote dist/review.md ({len(todo)} events and {len(unv)} locations to verify)")


# ----------------------------------------------------------------------------- add
def _ask(prompt, default=None, choices=None, required=True):
    hint = f" [{default}]" if default is not None else ""
    if choices:
        hint += f" ({'/'.join(choices)})"
    while True:
        v = input(f"{prompt}{hint}: ").strip() or (default if default is not None else "")
        if choices and v and v not in choices:
            print(f"  choose one of: {', '.join(choices)}")
            continue
        if required and v == "":
            continue
        return v


def slug(s: str) -> str:
    s = s.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "è": "e", "à": "a"}))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _append(path: Path, item: dict):
    if not path.exists():
        path.write_text("defaults:\n  status: needs_verification\n  languages: [de]\n\nitems:\n", encoding="utf-8")
    dumped = yaml.safe_dump([item], allow_unicode=True, sort_keys=False, width=120)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n" + "\n".join("  " + line if line else line for line in dumped.splitlines()) + "\n")


def cmd_add(args):
    """Interactive entry. New records land in data/events/inbox.yaml (or providers/locations.yaml)."""
    schema = json.loads((DATA / "schema" / "schema.json").read_text(encoding="utf-8"))
    db = load()
    P = {p["id"] for p in db["providers"]}
    L = {l["id"] for l in db["locations"]}
    print("Add an offer/event. Existing providers:", ", ".join(sorted(P)[:12]), "…")
    title = _ask("Title (de)")
    provider = _ask("Provider id (existing) or new provider name")
    if provider not in P:
        pid = slug(provider)
        ptype = _ask("Provider type", "verein", schema["provider"]["properties"]["type"]["enum"])
        website = _ask("Provider website", required=False)
        prov = {"id": pid, "name": provider, "type": ptype,
                "verification": {"status": "reported", "checked_at": TODAY.isoformat(), "method": "manual"}}
        if website:
            prov["website"] = website
        _append(DATA / "providers.yaml", prov)
        provider = pid
    loc = _ask("Location id (existing), new location name, or '-' for none", "-")
    locs = []
    if loc != "-":
        if loc not in L:
            lid = slug(loc)
            street = _ask("Street + number", required=False) or None
            zipc = _ask("PLZ", required=False) or None
            kreis = _ask("Kreis 1–12", required=False)
            _append(DATA / "locations.yaml", {
                "id": lid, "name": loc, "kind": _ask("Location kind", "andere", schema["location"]["properties"]["kind"]["enum"]),
                "address": {"street": street, "zip": zipc, "city": "Zürich"}, "kreis": int(kreis) if kreis else None,
                "quartier": _ask("Quartier", required=False) or None,
                "verification": {"status": "reported", "checked_at": TODAY.isoformat(), "method": "manual"}})
            loc = lid
        locs = [loc]
    kind = _ask("Schedule kind", "weekly", schema["event"]["properties"]["schedule"]["properties"]["kind"]["enum"])
    sch = {"kind": kind}
    if kind in ("weekly", "opening_hours"):
        sch["days"] = [d.strip() for d in _ask("Days (comma, e.g. mon,thu)").split(",")]
    if kind == "dates":
        sch["dates"] = [{"date": d.strip()} for d in _ask("Dates YYYY-MM-DD (comma separated)").split(",")]
    st = _ask("Start HH:MM", required=False)
    en = _ask("End HH:MM", required=False)
    if st:
        sch["start"] = st
    if en:
        sch["end"] = en
    free = _ask("Free?", "y", ["y", "n"]) == "y"
    price = {"free": free}
    if not free:
        amt = _ask("Price CHF (number, optional)", required=False)
        if amt:
            price["amount_chf"] = float(amt)
        price["text"] = _ask("Price text", required=False) or "kostenpflichtig"
    event = {
        "id": slug(f"{provider}-{title}"),
        "title": {"de": title},
        "provider": provider,
        "locations": locs,
        "category": _ask("Category", "baby", schema["event"]["properties"]["category"]["enum"]),
        "age": {"min_months": int(_ask("Min age in months", "0")), "max_months": int(_ask("Max age in months", "48"))},
        "schedule": sch,
        "price": price,
        "registration": _ask("Registration", "unknown", ["none", "required", "recommended", "unknown"]),
        "url": _ask("URL") or None,
        "status": "needs_verification",
        "verification": {"status": "reported", "checked_at": TODAY.isoformat(), "method": "manual"},
    }
    _append(DATA / "events" / "inbox.yaml", event)
    errors, _ = validate(load(), quiet=True)
    print(f"added '{event['id']}' to data/events/inbox.yaml" + ("" if not errors else f"\nfix these:\n" + "\n".join(errors)))


# ----------------------------------------------------------------------------- fetch (daily automation)
AGE_RX = re.compile(r"(ab|from)\s*(\d)\s*(J|Jahr|years|Monat|month)|(\d)\s*[–-]\s*(\d)\s*(J|Jahr|years)|baby|kleinkind|krabbel|toddler|eltern-kind|elki", re.I)


def _jsonld_events(raw: str) -> list[dict]:
    found = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', raw, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                t = node.get("@type")
                if t == "Event" or (isinstance(t, list) and "Event" in t) or str(t).endswith("Event"):
                    found.append(node)
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)
    return found


def _ical_events(raw: str) -> list[dict]:
    evs = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S):
        ev = {}
        for line in re.sub(r"\r?\n[ \t]", "", block).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                ev[k.split(";")[0]] = v
        evs.append({"name": ev.get("SUMMARY"), "startDate": ev.get("DTSTART"), "endDate": ev.get("DTEND"),
                    "location": ev.get("LOCATION"), "url": ev.get("URL"), "description": ev.get("DESCRIPTION")})
    return evs


def cmd_fetch(args):
    import urllib.request
    db = load()
    STATE.mkdir(exist_ok=True)
    state_file = STATE / "sources.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    known_urls = {e.get("url") for e in db["events"]}
    candidates = []
    for s in db["sources"]:
        if args.source and s["id"] != args.source:
            continue
        rec = state.setdefault(s["id"], {})
        try:
            req = urllib.request.Request(s["url"], headers={"User-Agent": "QuartierjournalBot/1.0 (+family events directory)"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8", "replace")
        except Exception as ex:  # noqa: BLE001 — record and continue
            rec.update(last_error=str(ex)[:200], last_attempt=TODAY.isoformat())
            print(f"✗ {s['id']}: {ex}")
            continue
        body = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S | re.I)
        h = hashlib.sha256(re.sub(r"\s+", " ", body).encode()).hexdigest()
        changed = rec.get("hash") not in (None, h)
        rec.update(hash=h, last_ok=TODAY.isoformat(), last_error=None, changed=changed or rec.get("hash") is None)
        evs = _jsonld_events(raw) if s["method"] in ("jsonld", "claude") else []
        if s["method"] == "ical" or raw.lstrip().startswith("BEGIN:VCALENDAR"):
            evs = _ical_events(raw)
        if s["method"] == "json_api":
            try:
                j = json.loads(raw)
                evs = j if isinstance(j, list) else j.get("events") or j.get("data") or []
            except json.JSONDecodeError:
                evs = []
        new = 0
        for ev in evs:
            blob = json.dumps(ev, ensure_ascii=False)
            if ev.get("url") in known_urls:
                continue
            if AGE_RX.search(blob) or s["kind"] != "aggregator":
                candidates.append({"source": s["id"], "found_at": TODAY.isoformat(), "event": ev})
                new += 1
        print(f"{'Δ' if changed else '·'} {s['id']}: {len(evs)} structured events, {new} candidates")
    state_file.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    (STATE / "candidates.json").write_text(json.dumps(candidates, ensure_ascii=False, indent=1), encoding="utf-8")
    changed = [k for k, v in state.items() if v.get("changed")]
    print(f"\n{len(candidates)} candidate events → data/state/candidates.json; "
          f"{len(changed)} sources changed since last run: {', '.join(changed) or '–'}")


def cmd_occurrences(args):
    db = load()
    names = {e["id"]: text(e["title"]) for e in db["events"]}
    for o in occurrences(db, weeks=args.weeks):
        print(f"{o['date']} {o['start'] or '     '} {names[o['event']]}{'' if o['explicit'] else '  (weekly)'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    b = sub.add_parser("build")
    b.add_argument("--weeks", type=int, default=8)
    sub.add_parser("report")
    sub.add_parser("add")
    f = sub.add_parser("fetch")
    f.add_argument("--source")
    o = sub.add_parser("occurrences")
    o.add_argument("--weeks", type=int, default=2)
    args = ap.parse_args()
    if args.cmd == "validate":
        errors, _ = validate(load())
        sys.exit(1 if errors else 0)
    {"build": cmd_build, "report": cmd_report, "add": cmd_add, "fetch": cmd_fetch,
     "occurrences": cmd_occurrences}[args.cmd](args)


if __name__ == "__main__":
    main()
