# moego-zoho

Syncs MoeGo Open API data for **all Hydrodog locations** into the Zoho Analytics
workspace **HDHQ Operations**, with an idempotent, repeatable refresh.

```
MoeGo Open API ──(moego_client.py + transform.py)──▶ CSV rows ──(zoho_workspace.py / zoho_loader.py)──▶ Zoho Analytics
      22 companies × 1 business each                         ./data/*.csv snapshot            HDHQ Operations
```

## How Hydrodog is set up in MoeGo

One Enterprise API key. Each franchise location is its own MoeGo **company**
(`cop…`) with exactly one **business** (`biz…`). `locations.csv` is the master
list (key, name, state, region, timezone, company_id, business_id) — add a row
when a new location opens; nothing else needs to change.

## Tables landed in Zoho Analytics

| Table | Grain | Key | Notes |
|---|---|---|---|
| `Locations` | one row per location | `id` (=location_key) | state/region/timezone + MoeGo business name & address |
| `Staff` | staff member | `id` | groomers/roles per location |
| `Services` | service catalog item | `id` | list price, minutes, category |
| `Customers` | customer | `id` | status, source, tags, last/next appt, primary address |
| `Pets` | pet | `id` | breed, type, weight, evaluation status, pet codes |
| `Appointments` | appointment | `id` | status, payment status, start/end (UTC + location-local), `appointment_date` (local), check-in/out, actual vs scheduled minutes, amounts, pet & service counts, primary staff |
| `AppointmentServices` | pet × service on an appointment | `id` | service, price, staff, pet — for revenue-by-service & groomer utilisation |
| `Orders` | order | `id` | subtotal, discount, tax, tips, total, paid, refunded, `sales_date` (local) |
| `OrderLineItems` | line item | `id` | SERVICE / PRODUCT / PACKAGE / NO_SHOW…, unit price, qty, tax |
| `Payments` | payment | `id` | method, status, module (GROOMING/RETAIL/MEMBERSHIP), amount, fee, refunds |
| `SyncLog` | sync run | `id` | when, mode, window, rows per table, failed locations — use for a "data freshness" tile |

Every fact row carries `location_key`, `location_name`, `company_id`, `business_id`.
All timestamps are `yyyy-MM-dd HH:mm:ss`; `*_utc` columns are UTC, `*_local` and
`*_date` columns use the location's timezone from `locations.csv`.

## Setup (one time)

```bash
cd ~/moego-zoho
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`.env` (git-ignored) needs:

* `MOEGO_API_KEY_B64` – the base64 API key (from the MoeGo API Mapping sheet)
* `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET` – a **Self Client** from https://api-console.zoho.com
* `ZOHO_ORG_ID` – Zoho Analytics → Settings → Organization
* `ZOHO_GRANT_CODE` – generated in the Self Client with scope
  `ZohoAnalytics.data.all,ZohoAnalytics.modeling.all,ZohoAnalytics.metadata.all`
  (valid 10 min). Then run **once**:

```bash
python zoho_auth.py        # swaps the grant code for ZOHO_REFRESH_TOKEN in .env
```

`ZOHO_WORKSPACE_ID` can stay blank: the sync finds or creates the workspace named
`ZOHO_WORKSPACE_NAME` (default `HDHQ Operations`).

## Running

```bash
python sync.py --full            # first load: full history from MOEGO_BACKFILL_START (2022-01-01)
python sync.py                   # incremental: everything updated since the last good run (2-day overlap)
python sync.py --since-days 7    # explicit look-back
python sync.py --dry-run         # extract to ./data/*.csv only (no Zoho writes)
python sync.py --locations APEX,OMAHA --tables Appointments,Orders
```

Re-running is safe: every table is upserted on `id` (`updateadd`); dimension
tables (`Locations`, `Staff`, `Services`) are fully replaced on a complete run.
`state.json` records the last successful run; delete it to force a full load.

## Nightly refresh

`run_sync.sh` wraps `sync.py` with dated logs in `./logs/`. Schedule it with launchd:

```bash
cp com.hydrodog.moego-zoho-sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.hydrodog.moego-zoho-sync.plist
launchctl start com.hydrodog.moego-zoho-sync      # run it now to test
```

It fires at 04:30 local time (or at next wake if the Mac was asleep). The
`SyncLog` table in Zoho shows every run, so a dashboard tile can flag stale data.

## Files

* `moego_client.py` – auth, pagination, retry, list helpers for every entity
* `transform.py` – MoeGo JSON → flat rows (money, timestamps, nested lists)
* `sync.py` – orchestrator: windows, per-location extraction, CSV snapshot, Zoho load, state
* `zoho_loader.py` – OAuth refresh + v2 bulk import (unchanged from the original loader)
* `zoho_workspace.py` – ensure workspace, list views, create-or-upsert tables
* `zoho_auth.py` – one-time grant-code → refresh-token exchange
* `locations.csv` – master location list
* `test_load.py` – smoke test for the Zoho path only
