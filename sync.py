#!/usr/bin/env python3
"""MoeGo -> Zoho Analytics sync for Hydrodog HQ ("HDHQ Operations" workspace).

Modes
-----
    python sync.py                 # incremental: everything updated since the last
                                   # successful run (minus a 2-day overlap)
    python sync.py --full          # full backfill from MOEGO_BACKFILL_START (default 2022-01-01)
    python sync.py --since-days 7  # explicit look-back window
    python sync.py --dry-run       # extract to ./data/*.csv only, no Zoho writes
    python sync.py --locations APEX,OMAHA --tables Appointments,Orders

Tables written (all keyed on ``id`` and upserted, so re-runs are idempotent):
    Locations, Staff, Services, Customers, Pets, Appointments,
    AppointmentServices, Orders, OrderLineItems, Payments, SyncLog
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

import moego_client as mg  # noqa: E402
import transform as tf  # noqa: E402

log = logging.getLogger("sync")

STATE_PATH = HERE / "state.json"
DATA_DIR = HERE / "data"
LOCATIONS_CSV = HERE / "locations.csv"
WORKSPACE_NAME = os.getenv("ZOHO_WORKSPACE_NAME", "HDHQ Operations")
BACKFILL_START = os.getenv("MOEGO_BACKFILL_START", "2022-01-01")
WINDOW_DAYS = int(os.getenv("MOEGO_WINDOW_DAYS", "90"))
OVERLAP_DAYS = 2

ALL_TABLES = ["Locations", "Staff", "Services", "Customers", "Pets", "Appointments",
              "AppointmentServices", "Orders", "OrderLineItems", "Payments"]
DIMENSION_TABLES = {"Locations", "Staff", "Services"}   # small; fully replaced each run


# --- helpers -------------------------------------------------------------------

def load_locations(only: set[str] | None = None) -> list[dict]:
    with open(LOCATIONS_CSV, newline="") as f:
        locs = [r for r in csv.DictReader(f)
                if r.get("company_id") and (r.get("active") or "true").lower() != "false"]
    if only:
        locs = [l for l in locs if l["location_key"] in only]
    return locs


def read_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except ValueError:
            pass
    return {}


def write_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def write_csv(name: str, rows: list[dict]) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{name}.csv"
    if not rows:
        path.write_text("")
        return path
    cols: dict[str, None] = {}
    for r in rows:
        for k in r:
            cols.setdefault(k, None)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cols), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def read_csv(name: str) -> list[dict]:
    path = DATA_DIR / f"{name}.csv"
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


NAMES_PATH = DATA_DIR / "_names.json"


def enrich_names(all_rows: dict[str, list[dict]]) -> None:
    """Add human-readable name columns next to customer/staff/pet ids.

    A persistent cache (data/_names.json) is merged with this run's rows so that
    incremental loads can still resolve ids that were not re-extracted.
    """
    cache: dict[str, dict[str, str]] = {"customers": {}, "staff": {}, "pets": {}}
    if NAMES_PATH.exists():
        try:
            cache.update(json.loads(NAMES_PATH.read_text()))
        except ValueError:
            pass
    for r in all_rows.get("Customers", []):
        if r.get("id"):
            cache["customers"][r["id"]] = " ".join(x for x in (r.get("first_name"), r.get("last_name")) if x).strip()
    for r in all_rows.get("Staff", []):
        if r.get("id"):
            cache["staff"][r["id"]] = r.get("full_name") or " ".join(
                x for x in (r.get("first_name"), r.get("last_name")) if x).strip()
    for r in all_rows.get("Pets", []):
        if r.get("id"):
            cache["pets"][r["id"]] = r.get("name") or ""
    DATA_DIR.mkdir(exist_ok=True)
    NAMES_PATH.write_text(json.dumps(cache))

    cust, staff, pets = cache["customers"], cache["staff"], cache["pets"]

    def names(ids: str, table: dict[str, str]) -> str:
        return " | ".join(table.get(i, i) for i in (ids or "").split(",") if i)

    for r in all_rows.get("Appointments", []):
        r["customer_name"] = cust.get(r.get("customer_id", ""), "")
        r["primary_staff_name"] = staff.get(r.get("primary_staff_id", ""), "")
        r["staff_names"] = names(r.get("staff_ids", ""), staff)
        r["pet_names"] = names(r.get("pet_ids", ""), pets)
    for r in all_rows.get("AppointmentServices", []):
        r["customer_name"] = cust.get(r.get("customer_id", ""), "")
        r["staff_name"] = staff.get(r.get("staff_id", ""), "")
    for t in ("Orders", "Payments"):
        for r in all_rows.get(t, []):
            if not r.get("customer_name"):
                r["customer_name"] = cust.get(r.get("customer_id", ""), "")
    for r in all_rows.get("Pets", []):
        r["owner_name"] = cust.get(r.get("customer_id", ""), "")
    for r in all_rows.get("Customers", []):
        r["full_name"] = cust.get(r.get("id", ""), "")


WAREHOUSE = Path(os.getenv("HDHQ_WAREHOUSE") or DATA_DIR / "hdhq.sqlite")


def upsert_sqlite(table: str, rows: list[dict], key: str = "id") -> int:
    """Upsert rows into the local SQLite warehouse (the dashboard reads this)."""
    import sqlite3
    if not rows:
        return 0
    DATA_DIR.mkdir(exist_ok=True)
    cols: dict[str, None] = {}
    for r in rows:
        for k in r:
            cols.setdefault(k, None)
    cols_l = list(cols)
    con = sqlite3.connect(WAREHOUSE)
    try:
        con.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ("{key}" TEXT PRIMARY KEY)')
        existing = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
        for c in cols_l:
            if c not in existing:
                con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}"')
        placeholders = ",".join("?" * len(cols_l))
        collist = ",".join(f'"{c}"' for c in cols_l)
        updates = ",".join(f'"{c}"=excluded."{c}"' for c in cols_l if c != key)
        con.executemany(
            f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders}) '
            f'ON CONFLICT("{key}") DO UPDATE SET {updates}',
            [tuple(None if r.get(c) is None else r.get(c) for c in cols_l) for r in rows if r.get(key)])
        con.commit()
    finally:
        con.close()
    return len(rows)


def dedupe(rows: list[dict], key: str = "id") -> list[dict]:
    seen: dict[str, dict] = {}
    for r in rows:
        k = r.get(key)
        if k:
            seen[str(k)] = r   # last write wins (later windows are newer)
    return list(seen.values())


# --- extraction ----------------------------------------------------------------

def extract_location(client: mg.MoeGoClient, loc: dict, start: datetime, end: datetime,
                     tables: set[str], companies_by_id: dict[str, dict]) -> dict[str, list[dict]]:
    cid, bid = loc["company_id"], loc["business_id"]
    out: dict[str, list[dict]] = {t: [] for t in ALL_TABLES}
    t0 = time.time()

    if "Locations" in tables:
        bizs = client.list_businesses(cid)
        biz = next((b for b in bizs if b.get("id") == bid), bizs[0] if bizs else None)
        if bizs and not any(b.get("id") == bid for b in bizs):
            log.warning("%s: business %s not returned by API (got %s)", loc["location_key"], bid,
                        [b.get("id") for b in bizs])
        out["Locations"].append(tf.location_row(loc, biz, companies_by_id.get(cid)))

    if "Staff" in tables:
        out["Staff"] = [tf.staff_row(s, loc) for s in client.list_staff(cid)]
    if "Services" in tables:
        out["Services"] = [tf.service_row(s, loc) for s in client.list_services(cid)]

    wins = list(mg.windows(start, end, WINDOW_DAYS))
    for i, (a, b) in enumerate(wins, 1):
        upd = mg.interval(a, b)
        log.info("%-15s window %d/%d  %s -> %s  (calls so far: %d)", loc["location_key"], i, len(wins),
                 a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d"), client.calls)
        if "Customers" in tables:
            out["Customers"].extend(tf.customer_row(c, loc) for c in client.list_customers(cid, upd))
        if "Pets" in tables:
            out["Pets"].extend(tf.pet_row(p, loc) for p in client.list_pets(cid, upd))
        if "Appointments" in tables or "AppointmentServices" in tables:
            for appt in client.list_appointments(cid, [bid], {"lastUpdatedTime": upd}):
                row, svc = tf.appointment_rows(appt, loc)
                out["Appointments"].append(row)
                out["AppointmentServices"].extend(svc)
        if tables & {"Orders", "OrderLineItems", "Payments"}:
            orders = list(client.list_orders(cid, [bid], upd))
            out["Orders"].extend(tf.order_row(o, loc) for o in orders)
            order_ids = [o["id"] for o in orders if o.get("id")]
            if order_ids and "OrderLineItems" in tables:
                li = client.list_order_line_items(cid, order_ids)
                taxes: dict[str, float] = {}
                for t in li.get("orderLineItemTaxes") or []:
                    if t.get("deleted"):
                        continue
                    taxes[str(t.get("orderItemId"))] = round(
                        taxes.get(str(t.get("orderItemId")), 0.0) + (tf.money(t.get("taxAmount")) or 0.0), 2)
                out["OrderLineItems"].extend(tf.order_line_item_row(x, loc, taxes)
                                             for x in li.get("orderLineItems") or [])
            if order_ids and "Payments" in tables:
                out["Payments"].extend(tf.payment_row(p, loc) for p in client.list_payments(cid, order_ids))

    for t in out:
        out[t] = dedupe(out[t])
    log.info("%-15s %5.1fs  " % (loc["location_key"], time.time() - t0) +
             "  ".join(f"{t[:5]}={len(v)}" for t, v in out.items() if v))
    return out


# --- main ------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help=f"backfill from {BACKFILL_START}")
    ap.add_argument("--since-days", type=float, help="look-back window in days")
    ap.add_argument("--locations", help="comma-separated location keys (default all)")
    ap.add_argument("--tables", help="comma-separated table names (default all)")
    ap.add_argument("--dry-run", action="store_true", help="extract to CSV only; skip Zoho")
    ap.add_argument("--load-only", action="store_true",
                    help="skip MoeGo; load Zoho from the existing ./data/*.csv (honours --locations/--tables)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    run_id = uuid.uuid4().hex[:12]
    started = datetime.now(timezone.utc)
    state = read_state()

    if args.full:
        start = datetime.fromisoformat(BACKFILL_START).replace(tzinfo=timezone.utc)
        mode = "full"
    elif args.since_days:
        start = started - timedelta(days=args.since_days)
        mode = f"since-{args.since_days:g}d"
    elif state.get("last_success_utc"):
        start = datetime.fromisoformat(state["last_success_utc"]) - timedelta(days=OVERLAP_DAYS)
        mode = "incremental"
    else:
        start = datetime.fromisoformat(BACKFILL_START).replace(tzinfo=timezone.utc)
        mode = "full (no prior state)"
    end = started + timedelta(minutes=5)

    tables = set(args.tables.split(",")) if args.tables else set(ALL_TABLES)
    unknown = tables - set(ALL_TABLES)
    if unknown:
        ap.error(f"unknown tables: {sorted(unknown)}; choose from {ALL_TABLES}")
    if "AppointmentServices" in tables:
        tables.add("Appointments")
    if tables & {"OrderLineItems", "Payments"}:
        tables.add("Orders")

    locs = load_locations(set(args.locations.split(",")) if args.locations else None)
    if not locs:
        ap.error("no locations selected")

    log.info("run %s  mode=%s  window=%s -> %s  locations=%d  tables=%s", run_id, mode,
             start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), len(locs), ",".join(sorted(tables)))

    all_rows: dict[str, list[dict]] = {t: [] for t in ALL_TABLES}
    failures: list[str] = []
    if args.load_only:
        enrich_names({t: read_csv(t) for t in ("Customers", "Staff", "Pets")})  # seed name cache from all rows
        keys = {l["location_key"] for l in locs}
        for t in ALL_TABLES:
            if t in tables:
                all_rows[t] = [r for r in read_csv(t) if r.get("location_key") in keys]
        mode = f"load-only ({mode})"
        log.info("loaded from CSV: %s", "  ".join(f"{t}={len(v)}" for t, v in all_rows.items() if v))
    else:
        client = mg.MoeGoClient()
        companies_by_id: dict[str, dict] = {}
        if "Locations" in tables:
            try:
                companies_by_id = {c["id"]: c for c in client.list_companies() if c.get("id")}
                missing = [l["location_key"] for l in locs if l["company_id"] not in companies_by_id]
                if missing:
                    log.warning("companies:list did not return these locations' company ids: %s", missing)
            except mg.MoeGoError as exc:
                log.warning("companies:list failed (%s); continuing without company names", exc)

        for loc in locs:
            try:
                part = extract_location(client, loc, start, end, tables, companies_by_id)
            except mg.MoeGoError as exc:
                log.error("%s FAILED: %s", loc["location_key"], exc)
                failures.append(loc["location_key"])
                continue
            for t, rows in part.items():
                all_rows[t].extend(rows)

        log.info("extracted (%d API calls): %s", client.calls,
                 "  ".join(f"{t}={len(v)}" for t, v in all_rows.items() if v))

    enrich_names(all_rows)
    for t, rows in all_rows.items():
        if t in tables and rows and not args.load_only:
            write_csv(t, rows)
    for t, rows in all_rows.items():
        if t in tables and rows:
            n = upsert_sqlite(t, rows)
            log.info("warehouse: %s upserted %d rows -> %s", t, n, WAREHOUSE.name)

    if args.dry_run:
        log.info("dry run: CSVs written to %s; skipping Zoho", DATA_DIR)
        return 1 if failures else 0

    import zoho_workspace as zw  # imported late so --dry-run works without Zoho creds
    ws_id = os.getenv("ZOHO_WORKSPACE_ID", "").strip()
    if not ws_id or set(ws_id) == {"0"}:
        ws_id = zw.ensure_workspace(WORKSPACE_NAME)
    views = zw.list_views(ws_id)
    loaded: dict[str, int] = {}
    for t in ALL_TABLES:
        if t not in tables or not all_rows[t]:
            continue
        # Dimension tables are fully replaced only when every location was extracted.
        full_replace = (t in DIMENSION_TABLES and not failures and not args.locations)
        res = zw.upsert_table(ws_id, t, all_rows[t], key="id", views=views, full_replace=full_replace)
        loaded[t] = res.get("rows", 0)

    finished = datetime.now(timezone.utc)
    status = "partial" if failures else "success"
    zw.upsert_table(ws_id, "SyncLog", [{
        "id": run_id, "started_utc": started.strftime(tf.TS_FMT), "finished_utc": finished.strftime(tf.TS_FMT),
        "duration_s": int((finished - started).total_seconds()), "mode": mode,
        "window_start_utc": start.strftime(tf.TS_FMT), "window_end_utc": end.strftime(tf.TS_FMT),
        "locations": len(locs) - len(failures), "failed_locations": ",".join(failures), "status": status,
        **{f"rows_{t.lower()}": n for t, n in loaded.items()},
    }], key="id", views=views)

    if not failures and not args.locations and not args.tables and not args.load_only:
        state["last_success_utc"] = started.isoformat()
    state["last_run"] = {"run_id": run_id, "mode": mode, "status": status, "finished_utc": finished.isoformat(),
                         "loaded": loaded, "failed_locations": failures}
    write_state(state)
    log.info("done: %s  %s", status, loaded)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
