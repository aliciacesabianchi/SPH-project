# Daily refresh – prompt for the scheduled Claude task

This is the prompt a scheduled Claude Code session runs every day against this repository.
The GitHub Action (`.github/workflows/events-data.yml`) does the mechanical part: it fetches
sources and flags changes. Claude does the part that needs reading: understanding a GZ programme
page, a church calendar or a PDF, and turning it into clean records.

---

You maintain the Quartierjournal database of offers for children aged 0–4 in the city of Zürich
(`data/`). Today is the date of this run. Work on a new branch `data/refresh-YYYY-MM-DD` and finish
with one pull request. Never push to `main`.

1. **Read the rules.** Read `data/README.md` and `data/schema/schema.json`. Run
   `python tools/qj.py validate` and `python tools/qj.py report`, and read `dist/review.md`.
2. **Pick today's sources.** From `data/sources.yaml`, take every `priority: high` source, plus
   about a third of the `medium` and `low` ones (rotate by weekday). Add any source listed as
   changed in `data/state/sources.json`, and every item in `data/state/candidates.json`.
3. **Check each source.** Use WebFetch on the URL, or WebSearch with `allowed_domains` set to the
   source's domain when fetching is blocked. Look for:
   - **New offers or dated events** suitable for ages 0–4 (min age ≤ 48 months), in the city of
     Zürich only (PLZ 80xx). Add them to the matching `data/events/*.yaml` file. Use
     `data/events/inbox.yaml` if unsure.
   - **Changes** to existing records: times, days, prices, season, age, location. Edit them in place.
   - **Cancelled or finished offers.** Set `status: ended`. Don't delete them, because the history
     is useful.
   - **Missing data** for records with `status: needs_verification` or locations marked
     `unverified`. Fill in what the source confirms.
4. **Be honest about certainty.** Set `verification.status: confirmed` only when you read it on the
   provider's own page in this run. Set `reported` when it comes from a search snippet or an
   aggregator (kikuka, zurini, kinderregion). Always set `checked_at` to today and `evidence` to
   the URL. Never invent addresses, times or prices: leave them `null` and explain in `note`.
5. **Avoid duplicates.** Before adding a record, search the YAML for the same provider, title and
   weekday. A recurring weekly offer is one record with `schedule.kind: weekly`, not one record
   per date. One-off events use `schedule.kind: dates`.
6. **Add new providers and locations** when you find them (a new Quartiertreff, Pfarrei,
   Spielgruppe, Indoorspielplatz). Add the provider's programme page to `sources.yaml` so it is
   checked from now on.
7. **Validate.** Run `python tools/qj.py validate` until there are 0 errors. Then run
   `python tools/qj.py build` and `python tools/qj.py report`.
8. **Open the PR.** Title it `Events refresh YYYY-MM-DD: +N new, M updated, K ended`. In the body,
   list what you added, what you changed and why, what you could not reach, and the top 10 open
   verification items from `dist/review.md`.

Aim for quality over volume. Ten correct, well-sourced records beat fifty guesses.
