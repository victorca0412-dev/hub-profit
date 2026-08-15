# HubProfit — Per-Driver Pay and Margin

**Date:** 2026-08-14
**Status:** Approved, ready for implementation planning

## Background

Multi-driver mode became usable for the first time in 2.0.0 — before that it was
documented but had no way to create a driver. A user who had asked for fluctuating
contracts tried it and pointed out that driver pay is modelled wrongly.

HubProfit charges driver pay as a **single flat per-day amount, shared by every
driver**. Sub-contracted delivery does not work that way. The owner is paid per
package and pays the driver a lower per-package rate, keeping the spread. A flat
day rate only approximates that, and badly on unusual days.

### Why this matters more with fluctuating contracts

On tiers of 1–20 → $2.25, 21–40 → $1.95, 41+ → $1.65, a driver paid a flat
$1.50 per package earns the owner:

| Packages | Owner receives | Driver paid | Margin per package | Day margin |
|---|---|---|---|---|
| 18 | $2.25 | $1.50 | $0.75 | $13.50 |
| 45 | $1.65 | $1.50 | $0.15 | $6.75 |

**The larger day makes less money.** At $1.70 per package the 45-package day loses
money outright. None of this is visible today.

## Goals

1. Driver pay is set per driver, as either a per-package or a per-day rate.
2. A driver's day shows the owner's margin, and says so plainly when it is negative.
3. Driver rates freeze into each entry, like every other rate in the app.

## Non-goals

### Drivers using the owner's vehicle — not modelled

Confirmed with the owner: drivers run their own vehicles. Their mileage is therefore
not the owner's cost, so **mileage-based expenses do not apply on driver-assigned
days** and the Log Day form hides the miles field for them.

If someone later runs a driver in the owner's vehicle, the honest answer is a
per-driver "uses my vehicle" flag, snapshotted per entry. Deliberately not built
now: it doubles the UI and the snapshot surface for a case that does not exist.

### The old global "Driver pay (per day)" expense — retired

Driver pay moves entirely onto the driver record, so the shared `driver` row in
`expense_config` is removed. This is safe **only because nothing uses it**: the lab
has zero driver-assigned entries, and multi-driver mode has been usable for less
than a day, so no user can have historical driver costs. The window to make this
change without disturbing a single stored number is now.

### Check-for-updates button — separate work

Requested in the same conversation. Different area, no shared code, and it carries
its own question about the app's "no cloud, no tracking" promise. It gets its own
spec.

---

## Design

### Where driver pay lives

`drivers` gains two columns:

```
drivers
  id, name, active, business_id     (existing)
  pay_model   TEXT NOT NULL DEFAULT 'per_package'   -- 'per_package' | 'per_day'
  pay_rate    REAL NOT NULL DEFAULT 0
```

Settings shows them inline, because a driver's cost belongs next to their name:

```
Drivers

  Alex     [$ 1.50 ]  [per package ▾]   [Rename]  [Deactivate]
  Sam      [$ 95.00]  [per day     ▾]   [Rename]  [Deactivate]

  [ New driver name        ]  [$ 0.00 ]  [per package ▾]   [Add driver]
```

Both models are supported rather than replacing one with the other: paying a flat
day rate is a real arrangement, and it is what the app does today.

### What an entry freezes

`daily_entries` gains:

```
  snap_driver_pay_model  TEXT     -- NULL when no driver was assigned
  snap_driver_pay_rate   REAL     -- NULL when no driver was assigned
```

Written by `create_entry` at save time, never touched by `update_entry`.

This is the same frozen-rate promise the app is built on, applied to a new rate.
Giving Alex a raise in March must not reprice January. Correcting a day's package
count *must* reprice that day's driver cost, because the count changed — against
that day's frozen rate, not today's.

### How a day is computed

`calculations` gains `driver_cost(entry)`:

- No driver assigned → `0`
- `snap_driver_pay_model == 'per_package'` → `packages × snap_driver_pay_rate`
- `snap_driver_pay_model == 'per_day'` → `snap_driver_pay_rate`

It appears in the expenses breakdown under the key `driver`, so it flows into
`total_expenses` and `net` with no change to the aggregation, the dashboard, the
history totals or the CSV.

**The owner's margin is therefore the existing `net`.** No second calculation and no
new column. What is missing today is not the number but the working, which is why
the visible part of this feature is presentation.

### Mileage expenses on driver days

`_expense_cost` returns `None` for `mileage_fuel` and `per_mile` modes when the entry
has a `driver_id`. The driver is running their own vehicle, so charging the owner for
those miles overstates their costs.

This changes computation logic rather than a snapshotted rate, so it applies to every
entry including past ones. That is acceptable here **only because there are zero
driver-assigned entries anywhere**. Verified on the lab before deciding. It must not
become a precedent for changing computation rules once data exists.

### Log Day

Selecting a driver hides the miles field and shows a short note explaining that the
driver's mileage is not the owner's cost. Miles are stored as `0` for that entry.

The live estimate gains the margin breakdown:

```
  ┌─ Estimated Earnings Preview ──────────────┐
  │  You earn         45 × $1.65      $74.25  │
  │                   tier 41+                │
  │  Driver pay       45 × $1.50     -$67.50  │
  │  ─────────────────────────────────────    │
  │  Your margin                       $6.75  │
  │                   $0.15 per package       │
  └───────────────────────────────────────────┘
```

and when the margin is negative:

```
  │  Your margin                     -$2.25   │   (red)
  │  This block loses money at Sam's rate     │
```

That warning is the point of the feature. A driver rate that is comfortably
profitable on small blocks can quietly lose money on large ones under a fluctuating
contract, and nothing surfaces it today.

Without a driver selected the estimate is unchanged.

### Migration v4

1. Add `pay_model` and `pay_rate` to `drivers`.
2. Add `snap_driver_pay_model` and `snap_driver_pay_rate` to `daily_entries`
   (nullable; existing rows stay NULL and compute a driver cost of zero, which is
   what they do today since none has a driver).
3. Delete the `driver` rows from `expense_config`.
4. `PRAGMA user_version = 4`.

Same rules as v2 and v3: explicit `BEGIN`, no `executescript`, no
`ALTER TABLE ... DROP COLUMN`.

---

## Testing

The existing 199 tests must continue to pass.

**Driver cost:**

- Per-package pay is `packages × rate`; per-day pay is the flat rate.
- No driver assigned yields zero regardless of stored rates.
- Editing a day's package count reprices per-package driver cost, and the entry's
  `snap_*` columns are unchanged afterwards.
- Changing a driver's rate in Settings does not alter any logged day.
- A per-day driver's cost does not change when the package count is corrected.

**Mileage on driver days:**

- Fuel and vehicle wear are not charged on a driver-assigned day.
- They are still charged on an owner-driven day with the same mileage.

**Margin:**

- A day where the driver rate exceeds the owner's effective rate produces a negative
  net, and History renders it as a loss.
- On a tiered contract, the same driver rate yields a healthy margin on a small block
  and a thin one on a large block — the table in Background, asserted.

**Migration:**

- A v3 database upgrades to v4 with every existing entry's computed net unchanged.
- The `driver` expense rows are gone afterwards.
- Existing entries have NULL driver snapshot columns and cost zero.

**Routes:**

- A driver can be created with a rate and model, and both round-trip.
- A blank or negative rate is rejected with an inline error and writes nothing.
- Selecting a driver on Log Day hides the miles input.

---

## Deployment

Ships migration v4. Portainer → Pull and redeploy; never tick "Remove volumes". The
`hubprofit_data` volume is now covered by the homelab snapshot job, so a verified
copy exists from the previous 03:30 run.
