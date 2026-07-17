# HubProfit — Timezone Fix, Entry Editing, and Tax Set-Aside

**Date:** 2026-07-16
**Status:** Approved, ready for implementation planning

## Background

Three changes, discovered from one report: an entry saved with the date 2026-07-17
when the user meant 2026-07-16.

The wrong date was a symptom. `app/main.py` calls `date.today()`, which reads the
container clock. `docker-compose.yml` sets no `TZ`, so the container runs UTC. The
user is in New Jersey (UTC-4 in July), so every evening after 8pm ET the Log Day
form pre-fills *tomorrow's* date. The bad row is the disease presenting, not the
disease.

The user separately asked for an entry edit button and a tax set-aside feature.
These compose: the edit button is how the bad row gets corrected, so no hand-written
SQL touches the deployed database.

## Goals

1. Stop the app from pre-filling the wrong date.
2. Let the user edit a logged day from the UI.
3. Show how much to set aside for taxes, computed on a basis that is actually correct
   for a mileage-heavy delivery business.
4. Get all of the above onto an already-populated deployed database safely.

## Non-goals

- Computing exact tax liability. This produces an estimate; the app will say so.
- Bracket/filing-status modelling. The app cannot see W-2 income, spouse income,
  or deductions, and would be confidently wrong if it pretended otherwise.
- Any refactoring not required by the above.

## Deployment context

The affected instance runs on the homelab Ubuntu host (192.168.0.221) as a Portainer
Git stack, with data in the `hubprofit_data` named volume. The local
`C:\Users\Victor\hub-profit\data\hub.db` is empty; it is a dev checkout.

HubProfit is also intended for public release, so nothing may hardcode the author's
locale or rates.

---

## Section 1 — Timezone

### Change

`docker-compose.yml` gains a configurable timezone, matching the existing `HOST_PORT`
pattern:

```yaml
environment:
  - TZ=${TZ:-America/New_York}
```

`Dockerfile` installs `tzdata` so the zone name resolves. Without it, glibc silently
falls back to UTC and the fix is a no-op that looks applied.

### Rationale

Configurable, not hardcoded: Amazon Hub Delivery is US-wide and other users are not
all Eastern. Default is `America/New_York` because that serves the primary user with
zero configuration.

### Verification

`date.today()` inside the running container returns the local date. Concretely: start
the container with `TZ=America/New_York` at a UTC time between 00:00 and 04:00, and
confirm the Log Day form pre-fills the previous calendar day.

---

## Section 2 — Entry editing

### Repo layer

`entries_repo.update_entry(conn, entry_id, data)`.

Writes exactly: `date`, `packages`, `miles`, `hours`, `extra_expense`, `driver_id`,
`note`.

**Never writes any `snap_*` column.** This is the load-bearing property of the whole
feature — see Rationale.

Returns whether a row matched, so the route can 404 on a bad id.

`entries_repo.get_entry` already exists and is reused as-is.

### Routes

| Route | Behaviour |
|---|---|
| `GET /log?edit=<id>` | Renders `log_day.html` pre-filled from the entry. Heading reads "Edit Day". Form posts to `/log/<id>`. 404 if the id does not exist. |
| `POST /log/<id>` | Calls `update_entry`, redirects 303 to `/history`. 404 if the id does not exist. |

`GET /log` with no `edit` param is unchanged: blank form, date defaults to today,
posts to `/log`.

### Template

`log_day.html` is reused, not duplicated. It receives an optional `entry` and
branches on it for the heading, the field `value=` attributes, and the form `action`.

`history.html` gains an **Edit** button in the existing actions cell beside Delete,
linking to `/log?edit={{ r.id }}`.

### Rationale

Reusing the Log Day form means one form to maintain, and the live earnings/fuel
estimate already built in `app/static/app.js` works on the edit path for free. A
separate edit template would duplicate that estimate logic permanently.

**On snapshot preservation:** every entry freezes `snap_pay_per_package`,
`snap_gas_price`, and `snap_mpg` at creation. The README sells this as "Your rates
are frozen in history — change your pay-per-package later and past days stay
correct." If editing re-snapshotted from current settings, then correcting a typo on
a January entry would silently reprice January at July's gas price. The user would
have no indication it happened. Fixing a typo must change only what was typed.

### Testing

- Editing a day changes the edited fields.
- **Editing a day after settings change does not alter `snap_*` values.** This is the
  regression test that protects the freeze promise.
- `GET /log?edit=<bad id>` returns 404.
- `POST /log/<bad id>` returns 404.
- `GET /log` with no param still renders a blank form defaulting to today.
- Editing an entry's date moves it between periods correctly (the reported bug's
  actual fix path).

---

## Section 3 — Tax set-aside

### The core problem

The app's `net` is not taxable profit, and for this business the gap is large.

`calculations.py` deducts actual fuel: `miles / snap_mpg * snap_gas_price`. On
Schedule C a delivery driver would typically instead take the IRS standard mileage
rate (~$0.70/mile for 2026), which covers fuel *and* depreciation, tires, insurance,
and maintenance in one number.

Worked example — 50 packages, 40 miles, 25 MPG, $3.40/gal, $1.65/package:

| | App net | Taxable profit |
|---|---|---|
| Earnings | $82.50 | $82.50 |
| Vehicle deduction | −$5.44 (actual fuel) | −$28.00 (40 × $0.70) |
| **Result** | **$77.06** | **$54.50** |

30% of $77.06 = $23.12 reserved, against a true ~$16.35. That over-reserves by ~41%.
A set-aside computed off `net` would be wrong in a way that looks entirely
reasonable, which is the worst kind of wrong.

Therefore the set-aside computes from its own tax basis, not from `net`.

### Settings

Two new columns on `settings`:

| Column | Default | Meaning |
|---|---|---|
| `tax_setaside_pct` | `30.0` | Percent of taxable profit to reserve |
| `irs_mileage_rate` | `0.70` | IRS standard mileage rate, $/mile |

Both editable on the Settings page. Help text states the 30% is approximately 15.3%
self-employment tax plus federal plus NJ state, and that it should be confirmed with
an accountant.

### Calculation

New pure function in `calculations.py`. No DB access, consistent with that module's
existing contract.

```
vehicle_deduction   = miles × snap_irs_mileage_rate
non_vehicle_expenses = insurance + phone + driver + extra_expense
tax_basis           = earnings − vehicle_deduction − non_vehicle_expenses
set_aside           = max(0, tax_basis) × tax_setaside_pct / 100
```

**`fuel` and `vehicle_wear` are excluded from `non_vehicle_expenses`.** The standard
mileage rate already covers both. Including them would double-count the vehicle,
which is precisely the error this design exists to avoid.

`max(0, tax_basis)` floors the reserve at zero: a losing month must not produce a
negative set-aside that offsets a winning one within the same period.

### Per-entry snapshot

`daily_entries` gains `snap_irs_mileage_rate`, snapshotted at creation from settings,
following the module's established `snap_*` pattern.

The IRS rate changes annually. Without the snapshot, updating the rate to the 2027
figure would silently reprice every 2026 entry's tax basis. With it, a 2026 entry
keeps the 2026 rate permanently. Same reasoning as the existing gas-price freeze, and
`update_entry` preserves it for the same reason it preserves the others.

### Display

- Dashboard: a "Set aside for taxes" tile beside net profit, respecting the active
  period filter.
- Dashboard: a quarterly breakdown aligned to estimated-payment due dates
  (Apr 15 / Jun 15 / Sep 15 / Jan 15), because that is when the money is actually
  owed.
- Both carry a visible note that the figure is an estimate, not tax advice.

### Testing

- Tax basis uses the mileage rate, not actual fuel.
- `fuel` and `vehicle_wear` are excluded from the basis; `insurance`, `phone`,
  `driver`, and `extra_expense` are included.
- Negative basis floors the set-aside at zero.
- The snapshotted rate is used, not the current settings rate.
- Quarterly grouping puts entries in the correct quarter, including on boundary dates
  (Mar 31 / Apr 1, Dec 31 / Jan 1).
- The worked example above reproduces exactly: basis $54.50, set-aside $16.35.

---

## Section 4 — Migration

### The problem

`db.py::init_db` runs `CREATE TABLE IF NOT EXISTS` only. There is no migration path.
On the deployed database — which already holds real entries — the three new columns
would simply never appear, and the app would crash on first query against them.

### Change

`init_db` gains an additive migration step that runs after `executescript(SCHEMA)`:

1. Read `PRAGMA table_info(<table>)` for `settings` and `daily_entries`.
2. `ALTER TABLE ... ADD COLUMN` for any of the three new columns that are absent.
3. Backfill `snap_irs_mileage_rate` on pre-existing entries with the 2026 default
   (0.70).

Idempotent, and correct on both a fresh database and the populated deployed one. The
`SCHEMA` string is updated in parallel so new installs get the columns directly.

### Rationale

Backfilling existing rows with the 2026 rate is a deliberate approximation: those
entries predate the column, and 0.70 is the correct rate for the tax year they fall
in. Entries from an earlier tax year would need a different rate, but none exist —
the deployed data starts in 2026.

### Testing

- Migration against a schema-without-the-columns database adds them.
- Running `init_db` twice is a no-op the second time.
- Existing rows receive the backfilled rate.
- A fresh database gets the columns from `SCHEMA` and needs no ALTER.

---

## Sequencing

1. Migration (Section 4) — everything else depends on the columns existing.
2. Timezone (Section 1) — independent, stops the bug recurring.
3. Edit (Section 2) — unblocks the user's date correction.
4. Tax (Section 3) — largest surface, depends on the migration.

After deploy the user corrects the 2026-07-17 entry to 2026-07-16 through the UI. No
manual SQL is run against the deployed volume.

## Deployment note

Per established homelab practice, deploying this stack requires
`git pull && docker compose build && docker compose up -d` — the build step is
mandatory, and "Remove volumes" must never be checked on redeploy, as it would
destroy `hubprofit_data`.

## Disclaimer

The tax feature produces an estimate. The 30% default and the standard-mileage
assumption are reasonable starting points for a single-member LLC (a disregarded
entity, taxed as pass-through on Schedule C) running Amazon Hub Delivery, but whether
standard mileage beats actual expenses in a given year, and the user's true effective
rate, are questions for an accountant. The UI states this rather than implying
precision it does not have.
