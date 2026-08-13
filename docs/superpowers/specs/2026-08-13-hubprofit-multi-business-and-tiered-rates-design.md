# HubProfit — Multiple Businesses, Fluctuating Contracts, and Five Bug Fixes

**Date:** 2026-08-13
**Status:** Approved, ready for implementation planning

## Background

HubProfit has been public on GitHub since 2026-06-28 and deployed on the author's
homelab since 2026-07-06. Two requests have come in from users running it, plus a
bug audit turned up five defects.

**Request 1 — multiple Hub accounts.** Some owners hold more than one Amazon Hub
Delivery account and want to track them in one HubProfit instance rather than
standing up a second container with a second volume.

**Request 2 — fluctuating pay contracts.** Amazon's earlier Hub Delivery contract
paid a per-package rate that varied with the size of the block offered. HubProfit
today assumes one flat rate forever.

The two are not independent. A pay contract belongs to a Hub account, so the
multi-business container has to exist before a per-account contract can live in it.

## Goals

1. Fix five confirmed bugs.
2. Let one HubProfit instance hold several businesses and switch between them.
3. Support a fluctuating (tiered) per-package contract, per business.

## Non-goals

Recorded so they are not rediscovered as gaps.

### Combined cross-business rollup — dropped

Considered and declined. Each business stays a self-contained book; there is no
"all businesses" total anywhere. Two Hub accounts under one instance are still two
sets of books, and a rollup would invite cross-business expense double-counting
(one insurance policy charged in full to both). The user adds the two numbers
themselves if they want a combined figure.

### Per-business vehicles — deliberately global for now

Vehicle, MPG, and gas price stay global and apply to every business. The user runs
both accounts off one car, and duplicating the vehicle picker per business would be
retyping with no benefit.

The known limitation: this is wrong the day someone runs a second account on a
different vehicle. It is an accepted trade, not an oversight.

The door is left open cheaply. `daily_entries` already snapshots `snap_mpg` and
`snap_gas_price` per entry, so moving the vehicle fields to per-business later is
purely additive and rewrites no history. Nothing in this design forecloses it.

### Auto-splitting shared monthly costs — dropped

Considered: mark insurance as "shared" and divide it across the businesses worked
that month. Declined because each business's past numbers would then move whenever
a day was logged in the *other* business, which contradicts the frozen-rate promise
the app is built on. Users split shared costs by hand; Help/FAQ explains why.

### Graduated (bracket) tier math — not built

Only whole-block lookup is implemented. See "Tier semantics" below.

---

## Part 0 — Bug fixes

These are independent of the features and ship first, as their own merge, so the
deployed instance gets the driver fix without waiting on the schema work.

### Bug A — the multi-driver feature is unreachable

`settings.html` offers "Enable multi-driver mode". `log_day.html` renders a Driver
dropdown. `help.html` documents assigning entries to drivers and charging driver pay.
But no route anywhere calls `drivers_repo.add_driver` or `set_driver_active` — both
are dead code. The dropdown can only ever contain "Me", and the "Driver pay (per day)"
expense, which fires only when `driver_id` is set, can never fire.

**Fix:** driver management UI in Settings — add a driver, rename, deactivate,
reactivate. Scoped per business once Part 1 lands.

### Bug B — a blank pay field silently resets the rate to $1.65

`main.py` reads rate fields as `_f(form.get("pay_per_package"), 1.65)`. `_f` returns
its default for blank *and* for unparseable input, so clearing the field and saving
silently rewrites the rate to Amazon's default. Confirmed:

```
rate after setting 2.50   -> 2.5
rate after submitting ""  -> 1.65
```

Same shape for `gas_price_per_gal` (→ 3.40) and `vehicle_mpg` (→ 25.0).

This is the most damaging of the five. Rates freeze into each entry at save time, so
every day logged after the silent reset is permanently wrong and must be corrected
one entry at a time.

**Fix:** validate. A blank or unparseable numeric field re-renders the form with an
inline error and saves nothing. Never substitute a default for input the user
actually typed.

### Bug C — an invalid date writes a row that is invisible and undeletable

`POST /log` does not validate the date. Posting `date=not-a-date` returns 303 as
though it worked:

```
RAW rows in table:        [{'id': 1, 'date': 'not-a-date', 'packages': 10}]
visible via list_entries: []
history page shows it?    False
```

Every read filters `date >= '1970-01-01' AND date <= '9999-12-31'`. Compared as
text, `"not-a-date"` sorts above `"9999-12-31"`, so the row falls outside every
query. It cannot be seen, counted, or reached to delete.

**Fix:** validate `YYYY-MM-DD` and calendar-validity on `POST /log` and
`POST /log/{id}`; reject with 400 rather than writing.

Repairing rows that already leaked through is a separate concern and lands with
Part 1's migration, not here, because Part 0 ships no schema work. The two halves are
ordered deliberately: Part 0 stops new orphans being created, Part 1 rewrites any
existing orphan's date to the date portion of its `created_at` so it becomes visible
and editable. Shipping the validation first means the repair has a fixed set to
work on.

### Bug D — negative packages and miles are accepted server-side

`min="0"` is enforced only by the browser. `packages=-50` stores fine and produces
negative earnings.

**Fix:** server-side `>= 0` validation on packages, miles, hours, and extra expense.

### Bug E — `business_name` is write-only

It is saved by `POST /settings` and read back only into its own input box. It renders
nowhere else; the header hardcodes "HubProfit".

**Fix:** resolved structurally by Part 1, where the name becomes `businesses.name`
and appears in the header switcher.

---

## Part 1 — Multiple businesses

### Schema

```
settings                     stays a single global row, id = 1
  vehicle_year / make / model / mpg
  gas_price_per_gal
  track_hours
  active_business_id         NEW — which business is being viewed
  business_name              REMOVED — migrated to businesses.name
  pay_per_package            REMOVED — migrated, now per-business
  drivers_enabled            REMOVED — migrated, now per-business

businesses                   NEW
  id, name, archived
  rate_model                 'flat' | 'tiered'
  pay_per_package            used when rate_model = 'flat'
  drivers_enabled

expense_config               + business_id, PK becomes (business_id, key)
drivers                      + business_id
daily_entries                + business_id
```

`track_hours` stays global: it is a display preference, not a property of a contract.
`drivers_enabled` becomes per-business, since one account may use drivers and another
may not.

### Migration

This is HubProfit's first schema migration. `init_db` is currently
`CREATE TABLE IF NOT EXISTS` only, with no migration path, so one has to be built.

Requirements:

- **Idempotent.** Runs on every container start; does nothing after the first.
- **Atomic.** Wrapped in a single transaction. Any failure rolls back and leaves the
  existing database exactly as it was.
- **Non-destructive.** No entry is deleted or repriced.

Versioning uses `PRAGMA user_version`, which is stored in the database header and
available in every SQLite build. Migrating to multiple businesses is version 2;
tiered contracts (Part 2) will be version 3.

`init_db` always runs `CREATE TABLE IF NOT EXISTS` for the v1 shape and then applies
migrations. A brand-new database and a migrated one therefore converge on the
identical schema — there is no second code path whose drift could go unnoticed.

**No `ALTER TABLE ... DROP COLUMN` anywhere.** It requires SQLite 3.35+, and while
the dev box has 3.50, the container is `python:3.12-slim` and its SQLite version
cannot be checked from a Windows dev box with no Docker. That is precisely the shape
of the July timezone bug — an infra assumption that silently no-ops in the container
while looking applied locally. Tables that need columns removed are rebuilt instead,
using only `CREATE` / `INSERT ... SELECT` / `DROP` / `RENAME TO`, which every
relevant SQLite version supports.

Steps for version 2, in order:

1. Create `businesses`.
2. If `businesses` is empty, insert business #1 from the legacy `settings` row:
   `name` = `settings.business_name` (or `"My Hub Business"` when blank),
   `pay_per_package` = `settings.pay_per_package`, `rate_model` = `'flat'`,
   `drivers_enabled` = `settings.drivers_enabled`.
3. `ALTER TABLE ... ADD COLUMN business_id INTEGER NOT NULL DEFAULT 1` on `drivers`
   and `daily_entries`. The default backfills every existing row to business #1 in
   the same statement.
4. **Rebuild `expense_config`** with the composite primary key `(business_id, key)`,
   copying existing rows in as business #1.
5. **Rebuild `settings`** without `business_name`, `pay_per_package`, or
   `drivers_enabled`, and with `active_business_id` added, defaulting to 1. The copy
   in step 2 has already happened, so nothing is lost.
6. Repair Bug C orphans: any `daily_entries.date` not matching
   `[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]` is rewritten to the date portion of
   that row's `created_at`. This catches malformed dates, which is the shape the bug
   actually produced; a format-valid but impossible date such as `2026-02-30` is not
   detected, and no longer reachable now that Part 0 validates on write.
7. Set `PRAGMA user_version = 2`.

**Existing entries keep computing off `snap_pay_per_package` with `snap_rate_model =
'flat'`, exactly as they do today. The migration is arithmetically inert.** A
regression test asserts this against a database seeded in the pre-migration schema.

**One documented exception: repairing a Bug C orphan (step 6) does move numbers, and
must.** A monthly fixed cost is spread across the days worked that month. An orphan
was a real day worked that no query could see, so it was excluded from that divisor.
Making it visible adds a workday, and every day in that month gets a slightly smaller
share of the monthly cost — each day's net rises accordingly.

Verified: with a $150/month insurance cost and 25 visible July days, repairing one
orphan into a 26th day moves the insurance share from $6.00 to $5.769 and lifts every
July day's net by $0.2308.

The alternative — leaving orphans invisible so the arithmetic never moves — means
permanently under-reporting days worked and silently losing a day's earnings. The
respread is the correct answer, not a side effect to be suppressed. A test records
this so it is never "corrected" back.

Databases with no orphan rows, which is nearly all of them, see no change at all.

### The switcher

Active business is stored in `settings.active_business_id`, not a cookie, so it
behaves identically on every device the user opens the app from. The trade: switching
on a phone also switches on the desktop. For a single-user self-hosted app that is
acceptable and arguably correct.

```
┌───────────────────────────────────────────────────┐
│ HubProfit   [ JVC Vending Services  ▾ ]           │
│                ├ JVC Vending Services  ✓          │
│ Dashboard  Log│ ├ Newton Hub                       │
└───────────────│ └ ── Manage businesses… ──────────┘
```

`POST /business/switch` sets it and redirects back to the referring page.

**With exactly one business the switcher does not render at all.** Single-account
users — the large majority — see no change to the UI.

### Scoping

Every read and write is scoped to the active business: dashboard, history, CSV
export, log day, settings, expense config, drivers.

`entries_repo.distinct_workdays_in_month` becomes business-scoped. This is the
correct reading of the monthly-spread rule: a day worked under Hub A is a workday
for Hub A's book, and a day worked under both is a workday in each.

### Creating a business

A new business is created from **Settings → Manage businesses** with just a name. It
starts with `rate_model = 'flat'`, `pay_per_package = 1.65`, `drivers_enabled = 0`,
and **its own copy of the `DEFAULT_EXPENSES` rows seeded into `expense_config`** —
fuel enabled, everything else off, matching what a fresh install gets today.

Seeding matters: without it a new business has no `expense_config` rows at all, and
`compute_entry` iterates that config to build expenses, so every day logged under it
would silently report zero costs and an inflated net. The seed is what makes a second
business behave like a fresh install rather than a broken one.

Creating a business does not switch to it; the user switches deliberately.

### Archiving

Businesses are archived, never deleted, so their entries are never orphaned. An
archived business is hidden from the switcher. The last remaining active business
cannot be archived.

---

## Part 2 — Fluctuating (tiered) contracts

### Tier semantics — whole-block lookup

The total package count for the day selects exactly one tier, and every package that
day pays that tier's rate.

```
Tiers:  1–20 → $2.25    21–40 → $1.95    41+ → $1.65

45 packages  →  lands in 41+  →  45 × $1.65  =  $74.25
18 packages  →  lands in 1–20 →  18 × $2.25  =  $40.50
```

This matches how Amazon's block offers worked: the block's size set its rate, and
smaller blocks paid more per package. Graduated bracket math — first 20 at $2.25,
next 20 at $1.95, and so on — is explicitly **not** implemented. The two produce very
different numbers ($74.25 vs $92.25 on a 45-package block), so this is recorded here
to prevent a later "fix" toward the wrong one.

### Tier editor

```
Pay & Earnings — JVC Vending Services

  Contract type   ( ) Flat rate per package
                  (•) Fluctuating (rate depends on package count)

  Tiers                                    whole block pays one rate
  ┌────────────────────────────────────────────────────┐
  │  From      To         Rate                          │
  │  [  1 ]  – [ 20 ]    $ [ 2.25 ]              ✕      │
  │  [ 21 ]  – [ 40 ]    $ [ 1.95 ]              ✕      │
  │  [ 41 ]  –   ∞       $ [ 1.65 ]              ✕      │
  │                                                     │
  │  + Add tier                                         │
  └────────────────────────────────────────────────────┘
     A 45-package day pays 45 × $1.65 = $74.25
```

Rows chain: the first tier always starts at 1, and each subsequent "From" is derived
as the previous row's "To" + 1 and rendered read-only. Only "To" and "Rate" are
editable, and the final row is always open-ended.

**Gaps and overlaps are made structurally impossible rather than validated after the
fact.** There is no arrangement of the editor that produces an uncovered count.

Server-side validation still runs, because the form is reachable outside the browser:
tiers must be non-empty, ascending, start at 1, and carry non-negative rates.
Switching contract type to `flat` leaves existing tier rows in place, so toggling
back and forth does not destroy the table.

### Engine

`calculations.py` gains one function, and the earnings line in `compute_entry`
changes to call it. Nothing else in the module moves.

```python
def earnings_for(entry):
    if entry.get("snap_rate_model") == "tiered":
        rate = _rate_for_count(entry["packages"], entry.get("snap_rate_tiers") or [])
        if rate is not None:
            return entry["packages"] * rate
    return entry["packages"] * entry["snap_pay_per_package"]
```

`_rate_for_count(n, tiers)` returns the rate whose `min_packages <= n <=
max_packages`, treating a null max as unbounded, and `None` when nothing matches.

The `None` path falls back to `snap_pay_per_package`, which every entry carries. A
tiered entry cannot produce zero earnings because its tier table was malformed.

Zero packages earns zero under either model.

### Snapshotting

`create_entry` snapshots `snap_rate_model` and, when tiered, the **entire tier table**
as JSON in `snap_rate_tiers` — not the resolved rate.

`update_entry` continues to write no `snap_*` column, unchanged from the edit-entry
spec.

This is the load-bearing decision, and it is what makes editing behave correctly.
Correcting a day from 45 packages to 38 reprices it from $1.65 to $1.95 against that
day's frozen tier table. The rate moved because the count moved; the contract did not.
Changing tiers in Settings afterward still cannot touch that day.

Storing the resolved rate instead would freeze a 45-package rate onto a 38-package
day and silently overpay it.

### Log Day and History

The live estimate in `app/static/app.js` receives the tier table in the existing
`log-config` data blob and resolves the rate as the user types:

```
  Packages delivered  [ 45 ]

  ┌─ Estimated Earnings Preview ──────────────┐
  │  Earnings          45 × $1.65      $74.25 │
  │                    tier 41+               │
  │  Fuel cost (est.)                 -$4.66  │
  │  Net (before fixed costs)          $69.59 │
  └───────────────────────────────────────────┘
```

History and `history.csv` gain an effective-rate column. Under a fluctuating contract,
seeing which tier a day landed in is the point of the feature.

```
  Date         Pkgs   Rate    Miles   Earnings   Expenses     Net
  2026-08-12     45   $1.65    38.5     $74.25     $12.40   $61.85
  2026-08-11     18   $2.25    21.0     $40.50      $8.10   $32.40
```

The column renders for flat contracts too, where it simply repeats the flat rate.

---

## Testing

The existing 56 tests must continue to pass, unchanged where possible.

**Migration** — the highest-risk area, tested against a database built in the
pre-migration schema and populated with entries:

- Migration is idempotent: running it twice leaves the same state as once.
- Every pre-existing entry is assigned to business #1.
- **Every pre-existing entry's computed net is byte-identical before and after.**
- A legacy `business_name` becomes the first business's name; a blank one yields
  `"My Hub Business"`.
- Legacy `expense_config` rows survive with their enabled flags and amounts.
- A failure partway through rolls back and leaves the original database intact.
- A Bug C orphan row is repaired to its `created_at` date and becomes visible.

**Tier engine:**

- Whole-block lookup selects the right tier at boundaries: 20, 21, 40, 41.
- The open-ended final tier catches arbitrarily large counts.
- Zero packages earns zero.
- A count matching no tier falls back to `snap_pay_per_package`.
- A tiered entry edited to a different package count reprices against its own frozen
  table, and its `snap_*` columns are unchanged afterward.
- Changing tiers in Settings does not alter any existing entry's computed earnings.
- Flat-rate businesses are unaffected by the presence of tier rows.

**Multi-business isolation:**

- Entries logged under business A never appear in business B's dashboard, history,
  or CSV.
- Expense config and drivers are per-business.
- A newly created business is seeded with the default expense rows, and a day logged
  under it reports fuel cost rather than zero expenses.
- `distinct_workdays_in_month` counts only the active business's days.
- Switching the active business changes what every page renders.
- The switcher does not render when only one business exists.
- The last active business cannot be archived.

**Bug fixes:**

- A driver can be added, renamed, deactivated, and reactivated through the UI.
- Driver pay applies on a day with an assigned driver and not on one without.
- A blank or unparseable rate re-renders with an error and does not overwrite the
  stored rate.
- `POST /log` with a malformed date returns 400 and writes nothing.
- Negative packages, miles, hours, and extra expense are rejected.

---

## Deployment

Per established homelab practice, deploying requires
`git pull && docker compose build && docker compose up -d` — the build step is
mandatory. In Portainer this is **Pull and redeploy**, and **"Remove volumes" must
never be checked**; it would destroy the `hubprofit_data` volume.

Unlike the last two changes, **this one ships a schema migration**. It runs
automatically on container start, inside a transaction, and is idempotent.

The `hubprofit_data` volume should be backed up before the first redeploy carrying
Part 1. The migration is designed to roll back cleanly on failure, but a backup is
cheap and this is the first migration the app has ever run against live data.

README and Help/FAQ need updating for: managing businesses, the contract-type
setting, and the note that shared costs across businesses are split by hand.
