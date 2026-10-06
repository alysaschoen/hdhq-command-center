"""Zoho Analytics loader.

Pushes tabular data into Zoho Analytics tables via the v2 Bulk Import API.

Public surface:
    refresh_access_token()   -> mint a fresh OAuth access token from the refresh token
    load_table(...)          -> bulk-import rows into an existing table (view)
    create_table(...)        -> convenience helper used by the smoke test to spin up
                                a throwaway table (import into a not-yet-existing table)

Design notes
------------
The v2 Bulk Import API expects:
  * The import options in a JSON blob passed as a URL-encoded ``CONFIG`` *query
    parameter* (not in the multipart body).
  * The actual data as a ``multipart/form-data`` file part named ``FILE``.
  * ``Authorization: Zoho-oauthtoken <token>`` and ``ZANALYTICS-ORGID: <org>``
    headers.

Access tokens are short-lived (~1h) so we cache the token in-process and refresh
lazily when it is missing or within a small expiry buffer.
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.parse
from typing import Any, Iterable, Mapping, Optional, Sequence

import requests
from dotenv import load_dotenv

load_dotenv()

# --- Configuration (from environment / .env) ---------------------------------

CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
ORG_ID = os.getenv("ZOHO_ORG_ID")
WORKSPACE_ID = os.getenv("ZOHO_WORKSPACE_ID")

ACCOUNTS_URL = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com").rstrip("/")
ANALYTICS_URL = os.getenv("ZOHO_ANALYTICS_URL", "https://analyticsapi.zoho.com").rstrip("/")

# Refresh a bit before the real expiry to avoid racing the clock.
_EXPIRY_BUFFER_SECONDS = 120

DEFAULT_TIMEOUT = 60  # seconds


class ZohoError(RuntimeError):
    """Raised when Zoho returns a non-success response."""


# --- OAuth -------------------------------------------------------------------

# Simple in-process token cache: (access_token, absolute_expiry_epoch).
_token_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}


def _require(name: str, value: Optional[str]) -> str:
    if not value:
        raise ZohoError(
            f"Missing required config '{name}'. Set it in your .env "
            f"(see .env.example)."
        )
    return value


def refresh_access_token() -> str:
    """Exchange the stored refresh token for a new access token.

    Updates the in-process cache and returns the new access token.
    """
    client_id = _require("ZOHO_CLIENT_ID", CLIENT_ID)
    client_secret = _require("ZOHO_CLIENT_SECRET", CLIENT_SECRET)
    refresh_token = _require("ZOHO_REFRESH_TOKEN", REFRESH_TOKEN)

    resp = requests.post(
        f"{ACCOUNTS_URL}/oauth/v2/token",
        params={
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        },
        timeout=DEFAULT_TIMEOUT,
    )

    try:
        payload = resp.json()
    except ValueError:
        raise ZohoError(
            f"Token refresh returned non-JSON (HTTP {resp.status_code}): {resp.text[:500]}"
        )

    # Zoho returns HTTP 200 even for some errors, signalled by an "error" key.
    if resp.status_code != 200 or "error" in payload or "access_token" not in payload:
        raise ZohoError(f"Token refresh failed (HTTP {resp.status_code}): {payload}")

    access_token = payload["access_token"]
    expires_in = float(payload.get("expires_in", 3600))
    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = time.time() + expires_in
    return access_token


def get_access_token(force_refresh: bool = False) -> str:
    """Return a valid access token, refreshing lazily when needed."""
    token = _token_cache.get("access_token")
    expires_at = _token_cache.get("expires_at", 0.0)
    if force_refresh or not token or time.time() >= (expires_at - _EXPIRY_BUFFER_SECONDS):
        return refresh_access_token()
    return token


# --- Bulk import -------------------------------------------------------------


def _rows_to_csv(rows: Sequence[Mapping[str, Any]], columns: Optional[Sequence[str]]) -> str:
    """Serialize a list of dict rows to a CSV string.

    Column order is taken from ``columns`` if given, otherwise inferred from the
    union of keys across all rows (first-seen order).
    """
    if not rows:
        raise ValueError("No rows to load.")

    if columns is None:
        seen: dict[str, None] = {}
        for row in rows:
            for key in row.keys():
                seen.setdefault(key, None)
        columns = list(seen.keys())

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Zoho-oauthtoken {get_access_token()}",
        "ZANALYTICS-ORGID": _require("ZOHO_ORG_ID", ORG_ID),
    }


def _bulk_import(
    url: str,
    config: Mapping[str, Any],
    csv_data: str,
    *,
    filename: str = "data.csv",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """POST a bulk-import request and return the parsed JSON response.

    ``config`` is JSON-encoded and passed as a URL-encoded ``CONFIG`` query
    parameter; ``csv_data`` is sent as the ``FILE`` multipart part.
    """
    config_json = json.dumps(config, separators=(",", ":"))
    query = urllib.parse.urlencode({"CONFIG": config_json})
    full_url = f"{url}?{query}"

    files = {"FILE": (filename, csv_data.encode("utf-8"), "text/csv")}

    resp = requests.post(
        full_url,
        headers=_headers(),
        files=files,  # requests sets multipart/form-data + boundary
        timeout=timeout,
    )

    try:
        payload = resp.json()
    except ValueError:
        payload = {"_raw": resp.text}

    if resp.status_code not in (200, 201):
        raise ZohoError(f"Bulk import failed (HTTP {resp.status_code}): {payload}")

    return payload


def load_table(
    view_id: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    import_type: str = "updateadd",
    matching_columns: Optional[Iterable[str]] = None,
    columns: Optional[Sequence[str]] = None,
    on_error: str = "abort",
    auto_identify: bool = True,
    workspace_id: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Bulk-import ``rows`` into an existing table (view).

    Parameters
    ----------
    view_id:
        The target table's view ID.
    rows:
        Sequence of dict rows. Keys are column names.
    import_type:
        One of ``append``, ``truncateadd``, ``updateadd``. For ``updateadd`` you
        must supply ``matching_columns`` (the key used to detect existing rows).
    matching_columns:
        Column name(s) that identify an existing row for update. Required when
        ``import_type == "updateadd"``.
    columns:
        Optional explicit column order for the generated CSV.
    on_error:
        ``abort``, ``skiprow``, or ``setcolumnempty``.
    auto_identify:
        Let Zoho auto-detect data types from the CSV.

    Returns the parsed JSON response (import summary lives under
    ``result`` / ``response`` depending on API version).
    """
    ws = _require("ZOHO_WORKSPACE_ID", workspace_id or WORKSPACE_ID)

    if import_type == "updateadd" and not matching_columns:
        raise ValueError("import_type 'updateadd' requires matching_columns.")

    csv_data = _rows_to_csv(rows, columns)

    config: dict[str, Any] = {
        "importType": import_type,
        "fileType": "csv",
        "autoIdentify": "true" if auto_identify else "false",
        "onError": on_error,
    }
    if matching_columns:
        config["matchingColumns"] = list(matching_columns)

    url = f"{ANALYTICS_URL}/restapi/v2/workspaces/{ws}/views/{view_id}/data"
    return _bulk_import(url, config, csv_data, timeout=timeout)


def create_table(
    table_name: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    columns: Optional[Sequence[str]] = None,
    on_error: str = "abort",
    auto_identify: bool = True,
    workspace_id: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Create a new table in the workspace and seed it with ``rows``.

    Uses the workspace-level bulk-import endpoint with a ``tableName`` in CONFIG,
    which creates the table if it does not already exist. Handy for the smoke
    test; production syncs should target an existing table via ``load_table``.
    """
    ws = _require("ZOHO_WORKSPACE_ID", workspace_id or WORKSPACE_ID)
    csv_data = _rows_to_csv(rows, columns)

    config: dict[str, Any] = {
        "tableName": table_name,
        "importType": "truncateadd",
        "fileType": "csv",
        "autoIdentify": "true" if auto_identify else "false",
        "onError": on_error,
    }

    url = f"{ANALYTICS_URL}/restapi/v2/workspaces/{ws}/data"
    return _bulk_import(url, config, csv_data, timeout=timeout)
