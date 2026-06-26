# HubProfit

A free, self-hosted dashboard that tracks the *real* net profit of your Amazon Hub Delivery business — your earnings minus fuel, insurance, and other costs Amazon doesn't cover.

---

## What it does

- Logs each day's packages delivered and miles driven
- Auto-estimates your fuel cost using your vehicle's MPG (pulled free from the EPA database) and your current gas price
- Spreads fixed monthly costs (insurance, phone bill, etc.) across the days you actually work that month
- Shows net profit broken down by day, week, month, and year — with a chart
- Optional $/hour tracking so you know your true hourly rate
- CSV export for taxes and record-keeping
- Frozen historical rates — if you change your pay-per-package rate later, past days are not rewritten

---

## Screenshots

_Screenshots coming soon._

<!-- Add screenshots to docs/screenshots/ and reference them here, e.g.:
![Dashboard](docs/screenshots/dashboard.png) -->

---

## Quick Start (Docker)

You need [Docker](https://docs.docker.com/get-docker/) installed on your machine or server.

```bash
git clone https://github.com/victorca0412-dev/hub-profit.git
cd hub-profit
docker compose up -d
```

Open **http://localhost:8000** and go to **Settings** to enter your pay-per-package rate, pick your vehicle (MPG fills in automatically from the EPA database), set your gas price, and turn on the expenses you want counted.

That's it — start logging your days under **Log Day**.

---

## Updating

When a new version is available, pull the latest code and rebuild:

```bash
git pull && docker compose up -d --build
```

---

## Your data

All your data stays in a local Docker volume (`hubprofit_data`) on your own machine. Nothing is sent to any server — no accounts, no cloud, no tracking.

> **Important:** When redeploying or recreating the container, do **not** remove the named volume (`hubprofit_data`) or you will lose all your logged days. In Portainer, never check "Remove volumes" on redeploy.

---

## For developers

Want to run the app locally without Docker?

```bash
# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Start the development server (auto-reloads on code changes)
uvicorn app.main:app --reload
```

Open **http://localhost:8000**.

**Run the test suite:**

```bash
pytest
```

All 44 tests should pass.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Disclaimer

Not affiliated with or endorsed by Amazon. "Amazon Hub" is a trademark of Amazon.com, Inc.
