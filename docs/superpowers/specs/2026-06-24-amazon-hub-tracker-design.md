# Amazon Hub Delivery Profit Tracker — Design Spec

**Date:** 2026-06-24
**Owner:** Victor / JVC Vending Services LLC
**Status:** Approved design, pending implementation plan

## 1. Problem & Goal

As an Amazon Hub Delivery Partner (a "micro-DSP"), Victor delivers Amazon
packages and is paid a flat **$1.65 per delivered package**, paid weekly.
Amazon's own Hub app shows raw earnings but **does not account for the costs
the partner bears** — fuel, vehicle wear, insurance, phone, and (later) driver
pay. Without subtracting those, top-line earnings hide whether the work is
actually profitable.

**Goal:** A simple self-hosted web dashboard that tracks daily deliveries and
expenses and shows **true net profit** — per day, week, month, and year —
plus an optional effective hourly rate, so the owner can answer "is this worth
my time, and when should I hand it to a driver?"

**Secondary goal:** Be **trivially self-hostable** so other Hub owners can run
their own free copy on a Linux box with one command, each with their own rate,
vehicle, and expense settings.

## 2. Users

- **Primary:** Victor — logs each day at a desk, end of day.
- **Secondary:** Other independent Hub Delivery owners who self-host their own
  instance. Each instance is single-owner; their data stays on their machine.

This is **not** a multi-tenant SaaS. Each deployment serves one business.

## 3. Non-Goals

- Not rebuilding Amazon's Hub app or integrating with Amazon's systems
  (no official partner API exists).
- No multi-tenant accounts / cloud SaaS. One instance = one owner.
- No live "cheapest gas nearby" in v1 (see Stretch Features — no reliable free
  API exists; manual gas price is the default).
- No automatic mileage import — owner reads miles from whatever mileage tracker
  they use and types the number in.

## 4. Tech Stack & Deployment

- **Single Docker container.** One `docker compose up` to run.
- **Backend:** Python **FastAPI**, serving **server-rendered Jinja2** pages.
- **DB:** **SQLite** (file persisted via a Docker volume).
- **Frontend:** server-rendered HTML + a little vanilla JS / HTMX for live
  calculations; **Chart.js** (via bundled static file) for charts.
- **No separate frontend build step** — keeps install dead-simple for other
  owners, matches the "single container" decision.
- **Config:** all owner-specific settings live in the DB (Settings page), not
  in env files, so a non-technical owner never edits config to get started.
- **Persistence:** named Docker volume holds the SQLite file so data survives
  redeploys. (Lesson from prior projects: never wipe named volumes on redeploy.)

### Deployment shape
```
/                FastAPI app (Jinja templates + static assets)
/data/hub.db     SQLite, mounted from a named volume
docker-compose.yml + Dockerfile
```

## 5. Pages (Tabs)

1. **Dashboard** — Layout "B": a large **Net Profit hero** for the selected
   period, supporting stats (packages, $/hr, fuel est., avg/day), and a
   **net-profit-by-day bar chart**. Period switcher: Week / Month / Year / All.
2. **Log Day** — the daily entry form (see §7).
3. **History** — table of past daily entries (date, packages, earnings,
   expenses, net, $/hr), filterable by period; edit/delete a row; CSV export.
4. **Settings** — pay rate, vehicle + MPG lookup, gas price, expense toggles &
   amounts, options (hours tracking, enable drivers). See §8.
5. **Help / FAQ** — plain-language explanation of how the dashboard works, what
   each number means, how the fuel estimate is calculated, and how to fill in
   Settings. Written for other Hub owners new to the tool.

## 6. Data Model (SQLite)

- **settings** (single row): `business_name`, `pay_per_package` (default 1.65),
  `gas_price_per_gal`, `vehicle_year`, `vehicle_make`, `vehicle_model`,
  `vehicle_mpg`, `track_hours` (bool), `drivers_enabled` (bool).
- **expense_config** (one row per expense type): `key`
  (fuel | vehicle_wear | insurance | phone | driver | custom), `enabled` (bool),
  `mode` (per_mile | monthly | per_day), `amount`. Drives which costs subtract
  and how they are allocated.
- **daily_entries**: `id`, `date`, `driver_id` (nullable),
  `packages`, `miles`, `hours` (nullable), `extra_expense` (nullable),
  `note`. Stored as raw inputs; money is **computed**, not stored, so changing a
  rate/setting recalculates history consistently.
- **drivers**: `id`, `name`, `active`. Present in schema from day one; UI hidden
  until `drivers_enabled` is on (future-proofing the "add a driver later" need).

## 7. Log Day Form (daily workflow)

Owner enters:
- **Date** (defaults to today), **Driver** (optional; hidden unless enabled),
- **Packages delivered**,
- **Miles driven for deliveries** — labeled generically ("from your mileage
  tracker"), no specific app named,
- **Hours worked** (optional), **Extra expense today** (optional, one-off).

App auto-calculates and shows live before saving:
- Earnings = `packages × pay_per_package`
- Fuel estimate = `miles ÷ vehicle_mpg × gas_price_per_gal` (if fuel enabled)
- Daily share of fixed costs (see §9)
- **Net profit today** and **effective $/hr** (if hours tracked)

## 8. Settings (the calculation engine)

- **Pay & earnings:** `pay_per_package` ($1.65 default), business name.
- **Vehicle / fuel:** Year → Make → Model cascading dropdowns populated from the
  **EPA fueleconomy.gov web service** (free, no API key, JSON). On selection,
  fetch and store **combined MPG**. Editable **gas price** field.
- **Which expenses to count:** toggles, each optionally with an amount/mode —
  - Fuel (mileage estimate) — on by default
  - Vehicle wear / mileage — per-mile amount (e.g. $0.18/mi)
  - Insurance — monthly amount
  - Phone / data — monthly amount
  - Driver pay — used when a driver covers a day
- **Options:** track hours / show $/hr; enable drivers.

All expense toggles default to a sensible state but are fully owner-configurable
so each Hub owner models only the costs that apply to them.

## 9. Calculations

- **Earnings (day):** `packages × pay_per_package`.
- **Fuel estimate (day):** `miles ÷ mpg × gas_price` when enabled.
- **Vehicle wear (day):** `miles × per_mile_amount` when enabled.
- **Fixed monthly costs (insurance, phone):** allocated per day by spreading the
  monthly amount across the **number of days worked in that calendar month**
  (i.e., `monthly_amount ÷ days_worked_this_month`). This keeps each day's share
  fair regardless of how many days the owner works. Recomputed as entries are
  added (a month's per-day share firms up as the month fills in).
- **Driver pay (day):** subtracted on days a driver is assigned (per-day amount
  or rate × packages — configurable). Inactive until drivers enabled.
- **Net profit (day):** earnings − all enabled expenses − one-off extra expense.
- **Effective $/hr (day):** `net_profit ÷ hours` when hours present.
- **Period totals:** sums of the above across Week / Month / Year / All.

Money values are **derived at read time** from stored raw inputs + current
settings, so editing a rate or toggle correctly updates historical views.
(Trade-off noted: if an owner changes their rate mid-history, past nets shift to
the new rate. Acceptable for v1; a future enhancement could snapshot the rate
per entry.)

## 10. External Integration

- **fueleconomy.gov web service** — `/ws/rest/vehicle/menu/{year|make|model}`
  for the cascading pickers and vehicle detail for MPG. Free, keyless, JSON.
  Cache menu/MPG responses in the DB so the app works offline after first setup
  and to avoid repeat calls.

## 11. Stretch / Future Features (explicitly out of v1)

- **Cheapest-gas-nearby widget:** No reliable free station-level API exists
  (GasBuddy = no public API; freemium APIs give regional averages only).
  Design hook: an optional Settings field for the owner's *own* gas-price API
  key; if present, show a small "today's local gas" widget that can one-click
  update the gas price. Off by default; never required.
- **Driver expansion:** per-driver profit views, driver pay rules. Schema is
  already in place; only UI/reporting work remains.
- **Per-entry rate snapshot** so historical nets are immune to later rate
  changes.
- **Tax/income export** (yearly P&L PDF) — useful since funds flow through the
  business checking account.

## 12. Testing

- Unit tests for the calculation module (earnings, fuel estimate, fixed-cost
  allocation across varying days-worked, net, $/hr) — the financial core.
- Tests for the fueleconomy.gov client using recorded/mocked responses.
- A smoke test that the app boots, serves each tab, and round-trips a saved
  daily entry through the DB.

## 13. Success Criteria

- Logging a day takes under ~30 seconds.
- Dashboard correctly shows net profit (not just gross) for any period.
- A second Hub owner can `docker compose up`, set their rate/vehicle/expenses
  in Settings, and start logging — without editing code or config files.
- The Help/FAQ tab is enough for a non-technical owner to understand every
  number on the dashboard.
