# HubProfit — Edit a Logged Day

**Date:** 2026-07-16
**Status:** Approved, ready for implementation planning

## Background

A day was logged as 2026-07-17 when the user meant 2026-07-16. There is currently no
way to correct a logged entry — History offers only Delete, so fixing a typo means
deleting the day and re-entering it from scratch.

This spec adds an Edit button.

## Goal

Let the user correct a logged day from the UI: open the entry in the Log Day form,
change what's wrong, save it again.

The immediate use is correcting the 2026-07-17 entry to 2026-07-16 through the UI,
so no hand-written SQL is run against the deployed database.

## Non-goals

Two items were considered and deliberately dropped. Recorded here so they aren't
rediscovered as gaps later.

### Tax set-aside — dropped

The user's banking app (Found) already reports how much to set aside. Building a
second estimator would duplicate a solved problem and invite the two to disagree.

Consequence: no new `settings` or `daily_entries` columns are needed. That in turn
means **no database migration is needed** — `db.py::init_db` is
`CREATE TABLE IF NOT EXISTS` only, with no migration path, but this feature writes
exclusively to columns that already exist. It deploys clean onto the populated
volume with no schema change.

### Timezone fix — deferred, not rejected

The wrong date has a root cause: `app/main.py` calls `date.today()`, which reads the
container clock, and `docker-compose.yml` sets no `TZ`. The container runs UTC while
the user is in New Jersey (UTC-4 in July), so after 8pm ET the Log Day form pre-fills
*tomorrow's* date.

The user has chosen to address this separately. The consequence is that the form will
keep pre-filling the wrong date every evening, and Edit becomes the recurring
workaround rather than the fix. This is an accepted trade, not an oversight.

When it is picked up, the fix is `TZ=${TZ:-America/New_York}` in `docker-compose.yml`
plus `tzdata` in the `Dockerfile` — configurable rather than hardcoded, since
HubProfit is intended for public release and Hub drivers are not all Eastern. Without
`tzdata` installed, glibc silently falls back to UTC and the fix is a no-op that looks
applied.

---

## Design

### Repo layer

`entries_repo.update_entry(conn, entry_id, data)`

Writes exactly: `date`, `packages`, `miles`, `hours`, `extra_expense`, `driver_id`,
`note`.

**Never writes any `snap_*` column.** This is the load-bearing property of the
feature — see Rationale below.

Returns whether a row matched, so the route can 404 on an unknown id.

`entries_repo.get_entry` already exists and is reused as-is.

### Routes

| Route | Behaviour |
|---|---|
| `GET /log?edit=<id>` | Renders `log_day.html` pre-filled from the entry. Heading reads "Edit Day". Form posts to `/log/<id>`. 404 if the id does not exist. |
| `POST /log/<id>` | Calls `update_entry`, redirects 303 to `/history`. 404 if the id does not exist. |

`GET /log` with no `edit` param is unchanged: blank form, date defaults to today,
posts to `/log`. `POST /log` is unchanged.

### Templates

`log_day.html` is reused, not duplicated. It receives an optional `entry` and branches
on it for three things: the heading text, the field `value=` attributes, and the form
`action`.

`history.html` gains an **Edit** button in the existing actions cell beside Delete,
linking to `/log?edit={{ r.id }}`.

---

## Rationale

### Why reuse the Log Day form

One form to maintain, and the live earnings/fuel estimate already built in
`app/static/app.js` works on the edit path for free. A separate edit template would
duplicate that estimate logic permanently — two places to change, forever.

### Why editing must not re-snapshot frozen rates

Every entry freezes `snap_pay_per_package`, `snap_gas_price`, and `snap_mpg` at
creation. The README sells this: *"Your rates are frozen in history — change your
pay-per-package later and past days stay correct."*

If `update_entry` re-snapshotted from current settings, correcting a typo on a January
entry would silently reprice January at July's gas price, with nothing in the UI to
indicate it had happened. Fixing a typo must change only what was typed.

This is why `update_entry` enumerates its columns explicitly rather than accepting a
dict and writing whatever keys it finds.

---

## Testing

- Editing a day changes the edited fields.
- **Editing a day after a settings change does not alter its `snap_*` values.** This
  is the regression test that protects the freeze promise.
- Editing an entry's date moves it between periods correctly — the reported bug's
  actual fix path (2026-07-17 → 2026-07-16).
- `GET /log?edit=<unknown id>` returns 404.
- `POST /log/<unknown id>` returns 404.
- `GET /log` with no param still renders a blank form defaulting to today.
- `POST /log` with no id still creates a new entry.

The existing 44 tests must continue to pass.

---

## Deployment

Per established homelab practice, deploying this stack requires
`git pull && docker compose build && docker compose up -d` — the build step is
mandatory. "Remove volumes" must never be checked on redeploy; it would destroy the
`hubprofit_data` volume.

No schema change ships with this, so the deployed database needs no special handling.

After deploy, the user opens History, clicks Edit on the 2026-07-17 row, and changes
the date to 2026-07-16.
