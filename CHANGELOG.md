# Changelog

All notable changes to HubProfit are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases before 2.0.0 were public but never versioned. If you deployed HubProfit
before 2026-08-13, you are on that unversioned code — see the upgrade note below.

---

## [2.1.0] — 2026-08-14

### Added

- **Per-driver pay.** Each driver now has their own rate, set as either a
  per-package or a per-day amount, instead of one flat per-day figure shared by
  everyone. Sub-contracted delivery pays the driver out of your per-package rate,
  and this models that.
- **Your margin on driver days.** Log Day shows what you earn, what the driver
  costs, and what you keep — and says plainly when a block loses money at that
  driver's rate.

  This matters most on a fluctuating contract, where larger blocks pay you *less*
  per package. On tiers of 1–20/21–40/41+ at $2.25/$1.95/$1.65, a driver paid a
  flat $1.50 earns you $13.50 on an 18-package day and $6.75 on a 45-package one.
  At $1.70 the larger day loses money outright. None of that was visible before.

### Changed

- **Mileage is no longer recorded or charged on driver days.** Drivers run their
  own vehicles, so their miles are not your cost. Selecting a driver hides the
  miles field.
- **The shared "Driver pay (per day)" expense has been removed** from Settings.
  Driver pay lives on the driver now. Nothing is lost: multi-driver mode only
  became usable in 2.0.0, so no logged day can have used it.

### Upgrade notes

- Ships database version 4. It runs automatically, in one transaction, and does
  not change any day you have already logged.
- Portainer: **Pull and redeploy**. Never tick *Remove volumes*.

---

## [2.0.0] — 2026-08-13

The first release that changes the database. **Back up your `hubprofit_data`
volume before upgrading.**

### Added

- **Multiple Hub businesses.** Track more than one Hub Delivery account in one
  instance. A switcher appears in the header once you have a second business, and
  every page — Dashboard, Log Day, History, CSV export — shows only the business
  you have selected. Each keeps its own pay rate, expenses, drivers, and logged
  days. Vehicle, MPG, and gas price stay shared, since most owners drive one car.
- **Fluctuating pay contracts.** If your per-package rate depends on the block
  size, choose *Fluctuating* in Settings and enter your tiers. The day's total
  package count selects one tier and every package that day pays that tier's rate
  — a 45-package day on 1–20/21–40/41+ at $2.25/$1.95/$1.65 earns $74.25.
  Contract type is per business, so one account can be flat and another tiered.
- **Driver management.** Add, rename, deactivate, and reactivate drivers in
  Settings. Multi-driver mode was documented but had no way to create a driver.
- **Effective rate column** in History and the CSV export, so you can see which
  tier each day landed in.
- **Version display** in the page footer and on Help/FAQ, including the database
  schema version.

### Fixed

- **A blank pay-per-package field silently reset your rate to $1.65.** Rate fields
  fell back to a default for blank *and* unparseable input alike, with nothing in
  the UI to say so. Because rates freeze into each entry at save time, every day
  logged afterwards was permanently wrong. Bad values are now rejected and
  nothing is written.
- **An invalid date created a row you could not see or delete.** A malformed date
  passed validation and was stored, but every query filters on a date range, and
  such a value sorts outside it. The row was invisible to History, the dashboard,
  and the CSV, and unreachable for deletion. Dates are now validated on save, and
  the upgrade repairs any existing orphan to its creation date.
- **Negative packages and miles were accepted** by the server; the `min="0"` was
  enforced only by the browser.
- **Multi-driver mode could never be used.** Settings offered the toggle, Log Day
  rendered the dropdown, and Help documented driver pay — but no route existed to
  add a driver, so the dropdown could only ever say "Me" and the per-day driver
  expense could never apply.
- **Business name was saved but never shown** anywhere in the app.

### Changed

- Rejected forms now redisplay exactly what you typed, with the specific problem
  marked against the offending field, instead of silently discarding the entry.
- **Static files are now stamped with the app version.** Without this, upgrading
  left your browser running the previous release's cached JavaScript against the
  new pages — new markup, old behaviour, no error to tell you. If you have ever
  upgraded HubProfit and seen something behave oddly until a hard refresh, that
  was why.
- The database is now versioned and upgrades automatically on start. Upgrades run
  in a single transaction and roll back completely if anything fails.

### Upgrade notes

- **Back up `hubprofit_data` before redeploying.** The upgrade rebuilds two small
  tables (settings, expense config) to remove columns. Your logged days are only
  ever added to, never rebuilt, and the whole upgrade is transactional — but this
  is the first release to touch stored data, and a backup is cheap.
- In Portainer: **Pull and redeploy**. Never tick *Remove volumes*.
- Your logged days will not be repriced. The one exception: if you have an
  invisible row from the date bug above, repairing it adds a workday to that
  month, so that month's fixed costs spread slightly differently. That is the
  repair working — the day was always real, it just could not be seen.

---

## Unversioned — 2026-06 to 2026-07

The original public release and its follow-ups: daily logging, EPA-backed fuel
estimates, frozen historical rates, CSV export, the Docker deployment, editing a
logged day, and running the container in a real timezone rather than UTC.
