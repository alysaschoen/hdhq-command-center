"""Zoho Analytics workspace/table helpers (REST API v2) built on zoho_loader.

    ensure_workspace(name)  -> workspace id (creates the workspace if missing)
    list_views(ws)          -> {view_name: view_id}
    upsert_table(ws, name, rows, key) -> creates the table on first load,
                                        then updateadd's on ``key`` thereafter
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any, Mapping, Sequence

import requests

import zoho_loader as zl

log = logging.getLogger("zoho")

CHUNK_ROWS = 20000  # rows per bulk-import call


def _get(path: str, config: Mapping[str, Any] | None = None) -> dict:
    url = f"{zl.ANALYTICS_URL}/restapi/v2{path}"
    if config:
        url += "?" + urllib.parse.urlencode({"CONFIG": json.dumps(config, separators=(",", ":"))})
    resp = requests.get(url, headers=zl._headers(), timeout=zl.DEFAULT_TIMEOUT)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"_raw": resp.text}
    if resp.status_code != 200:
        raise zl.ZohoError(f"GET {path} failed (HTTP {resp.status_code}): {payload}")
    return payload


def _post(path: str, config: Mapping[str, Any]) -> dict:
    url = f"{zl.ANALYTICS_URL}/restapi/v2{path}?" + urllib.parse.urlencode(
        {"CONFIG": json.dumps(config, separators=(",", ":"))})
    resp = requests.post(url, headers=zl._headers(), timeout=zl.DEFAULT_TIMEOUT)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"_raw": resp.text}
    if resp.status_code not in (200, 201):
        raise zl.ZohoError(f"POST {path} failed (HTTP {resp.status_code}): {payload}")
    return payload


def list_workspaces() -> dict[str, str]:
    data = _get("/workspaces").get("data", {})
    out: dict[str, str] = {}
    for w in (data.get("ownedWorkspaces") or []) + (data.get("sharedWorkspaces") or []):
        out[w["workspaceName"]] = str(w["workspaceId"])
    return out


def ensure_workspace(name: str) -> str:
    existing = list_workspaces()
    if name in existing:
        log.info("Workspace '%s' exists (id %s)", name, existing[name])
        return existing[name]
    payload = _post("/workspaces", {"workspaceName": name,
                                    "workspaceDesc": "MoeGo data for all Hydrodog locations (auto-synced)"})
    ws_id = str(payload["data"]["workspaceId"])
    log.info("Created workspace '%s' (id %s)", name, ws_id)
    return ws_id


def list_views(ws_id: str) -> dict[str, str]:
    data = _get(f"/workspaces/{ws_id}/views").get("data", {})
    return {v["viewName"]: str(v["viewId"]) for v in data.get("views", [])
            if str(v.get("viewType", "Table")).lower() == "table"}


# --- explicit schema ------------------------------------------------------------

_MULTI = {"ticket_comment", "service_names", "tags", "pet_codes", "pet_ids", "staff_ids", "title",
          "working_business_ids", "failed_locations", "moego_business_name", "moego_company_name"}
_BOOL = {"no_show", "is_deleted", "available_all_business"}
_LOOKUPS = {  # column -> (table, column)
    "location_key": ("Locations", "location_key"),
    "customer_id": ("Customers", "id"),
    "pet_id": ("Pets", "id"),
    "appointment_id": ("Appointments", "id"),
    "order_id": ("Orders", "id"),
    "staff_id": ("Staff", "id"),
    "primary_staff_id": ("Staff", "id"),
    "created_by_staff_id": ("Staff", "id"),
    "service_id": ("Services", "id"),
}


def column_type(name: str) -> str:
    n = name.lower()
    if n in _BOOL:
        return "BOOLEAN"
    if n in _MULTI:
        return "MULTI_LINE"
    if n.endswith(("_utc", "_local", "_date")) or n == "birthday":
        return "DATE"
    if n.endswith(("_amount", "_price", "_fee", "_total")) or n in ("price", "sub_total", "amount"):
        return "CURRENCY"
    if n.endswith(("_lat", "_lng")) or n in ("lat", "lng"):
        return "DECIMAL_NUMBER"
    if n.endswith(("_minutes", "_count", "_s")) or n in ("quantity", "weight_value", "locations", "service_minutes") \
            or n.startswith("rows_"):
        return "NUMBER"
    return "PLAIN"


def create_table(ws_id: str, table: str, columns: Sequence[str], *, existing: dict[str, str]) -> str:
    """Create ``table`` with typed columns (+ lookups to tables that already exist). Returns view id."""
    cols = []
    for c in columns:
        col: dict[str, Any] = {"COLUMNNAME": c, "DATATYPE": column_type(c)}
        lk = _LOOKUPS.get(c)
        if lk and lk[0] in existing and lk[0] != table:
            col["LOOKUPCOLUMN"] = {"TABLENAME": lk[0], "COLUMNNAME": lk[1]}
        cols.append(col)
    design = {"tableDesign": {"TABLENAME": table, "TABLEDESCRIPTION": "MoeGo sync (auto-managed)", "COLUMNS": cols}}
    try:
        payload = _post(f"/workspaces/{ws_id}/tables", design)
    except zl.ZohoError as exc:
        log.warning("%s: create with lookups failed (%s); retrying without lookups", table, str(exc)[:200])
        for col in cols:
            col.pop("LOOKUPCOLUMN", None)
        payload = _post(f"/workspaces/{ws_id}/tables", design)
    view_id = str(payload.get("data", {}).get("viewId") or payload.get("data", {}).get("view_id") or "")
    if not view_id:
        view_id = list_views(ws_id)[table]
    log.info("%s: created table with %d typed columns", table, len(cols))
    return view_id


def _chunks(rows: Sequence[Mapping[str, Any]], n: int):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def _columns(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for r in rows:
        for k in r:
            seen.setdefault(k, None)
    return list(seen)


def upsert_table(ws_id: str, table: str, rows: Sequence[Mapping[str, Any]], key: str = "id",
                 *, views: dict[str, str] | None = None, full_replace: bool = False) -> dict[str, Any]:
    """Load ``rows`` into ``table``; create it on first sight.

    * Table missing  -> created via workspace-level import (truncateadd).
    * Table present  -> ``updateadd`` matched on ``key`` (or ``truncateadd`` when
      ``full_replace`` is set, e.g. for small dimension tables).
    """
    if not rows:
        log.info("%s: no rows, skipping", table)
        return {"rows": 0}
    views = views if views is not None else list_views(ws_id)
    columns = _columns(rows)
    total = 0
    date_cols = [c for c in columns if c.endswith("_date") or c in ("birthday",)]
    date_cfg: dict[str, Any] = {"dateFormat": "yyyy-MM-dd HH:mm:ss"}
    if date_cols:
        date_cfg["columnDateFormat"] = {c: "yyyy-MM-dd" for c in date_cols}

    if table not in views:
        views[table] = create_table(ws_id, table, columns, existing=views)
        import_type = "updateadd"
    else:
        import_type = "truncateadd" if full_replace else "updateadd"

    view_id = views[table]
    for i, chunk in enumerate(_chunks(rows, CHUNK_ROWS)):
        csv_data = zl._rows_to_csv(chunk, columns)
        cfg: dict[str, Any] = {"importType": import_type if i == 0 else "updateadd", "fileType": "csv",
                               "autoIdentify": True, "onError": "setcolumnempty", **date_cfg}
        if cfg["importType"] == "updateadd":
            cfg["matchingColumns"] = [key]
        zl._bulk_import(f"{zl.ANALYTICS_URL}/restapi/v2/workspaces/{ws_id}/views/{view_id}/data", cfg,
                        csv_data, filename=f"{table}.csv", timeout=300)
        total += len(chunk)
    log.info("%s: loaded %d rows (%s)", table, total, import_type)
    return {"rows": total, "view_id": view_id}


def delete_table(ws_id: str, view_id: str) -> None:
    url = f"{zl.ANALYTICS_URL}/restapi/v2/workspaces/{ws_id}/views/{view_id}"
    resp = requests.delete(url, headers=zl._headers(), timeout=zl.DEFAULT_TIMEOUT)
    if resp.status_code not in (200, 204):
        raise zl.ZohoError(f"DELETE view {view_id} failed (HTTP {resp.status_code}): {resp.text[:300]}")
