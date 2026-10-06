"""MoeGo Open API client.

Thin wrapper over https://openapi.moego.pet with:
  * Basic auth using the base64-encoded API key (``MOEGO_API_KEY_B64``)
  * automatic pagination (``pageToken`` / ``nextPageToken``)
  * retry with backoff on 429 / 5xx / transient network errors
  * list helpers for every entity the HDHQ sync needs

Hydrodog's MoeGo Enterprise setup: ONE API key, and each franchise location is
its own MoeGo *company* (``cop...``) with exactly one *business* (``biz...``)
under it. Every list call therefore takes a ``company_id`` and we loop over
locations in ``sync.py``.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

import requests

log = logging.getLogger("moego")

BASE_URL = os.getenv("MOEGO_BASE_URL", "https://openapi.moego.pet").rstrip("/")
PAGE_SIZE = 500          # API max
MAX_RETRIES = 6
TIMEOUT = 60
_RETRY_STATUS = {429, 500, 502, 503, 504}


class MoeGoError(RuntimeError):
    pass


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def interval(start: datetime, end: datetime) -> dict[str, str]:
    return {"startTime": _iso(start), "endTime": _iso(end)}


def windows(start: datetime, end: datetime, days: int) -> Iterator[tuple[datetime, datetime]]:
    """Yield consecutive [a, b) windows of ``days`` covering start..end."""
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


class MoeGoClient:
    def __init__(self, api_key_b64: Optional[str] = None, *, min_interval_s: float = 0.25):
        key = api_key_b64 or os.getenv("MOEGO_API_KEY_B64")
        if not key:
            raise MoeGoError("MOEGO_API_KEY_B64 is not set (see .env.example).")
        self._s = requests.Session()
        self._s.headers.update({
            "Authorization": f"Basic {key.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._min_interval = min_interval_s
        self._last_call = 0.0
        self.calls = 0

    # -- low level ---------------------------------------------------------

    def post(self, path: str, body: dict[str, Any], *, max_retries: int = MAX_RETRIES) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        delay = 1.0
        for attempt in range(1, max_retries + 1):
            gap = self._min_interval - (time.time() - self._last_call)
            if gap > 0:
                time.sleep(gap)
            try:
                resp = self._s.post(url, json=body, timeout=TIMEOUT)
            except requests.RequestException as exc:
                if attempt == max_retries:
                    raise MoeGoError(f"{path}: network error after {attempt} tries: {exc}") from exc
                log.warning("%s network error (%s); retry in %.0fs", path, exc, delay)
                time.sleep(delay); delay = min(delay * 2, 30)
                continue
            finally:
                self._last_call = time.time()
                self.calls += 1

            if resp.status_code in _RETRY_STATUS and attempt < max_retries:
                wait = float(resp.headers.get("Retry-After") or delay)
                log.warning("%s HTTP %s; retry in %.0fs", path, resp.status_code, wait)
                time.sleep(wait); delay = min(delay * 2, 30)
                continue
            if resp.status_code != 200:
                raise MoeGoError(f"{path} HTTP {resp.status_code}: {resp.text[:500]}")
            try:
                data = resp.json()
                n = next((len(v) for v in data.values() if isinstance(v, list)), 0)
                log.debug("%s page=%s -> %d items", path, (body.get("pagination") or {}).get("pageToken"), n)
                return data
            except ValueError as exc:
                raise MoeGoError(f"{path}: non-JSON response: {resp.text[:300]}") from exc
        raise MoeGoError(f"{path}: exhausted retries")

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        resp = self._s.get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT)
        self.calls += 1
        if resp.status_code != 200:
            raise MoeGoError(f"{path} HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    def paginate(self, path: str, body: dict[str, Any], key: str) -> Iterator[dict[str, Any]]:
        """Iterate every item under ``key`` across all pages.

        MoeGo's ``pageToken`` is a 1-based page *number* and must not be empty
        (the API rejects a missing/empty token). ``nextPageToken`` is empty on
        the last page; as a fallback we also advance the page number ourselves
        whenever a full page came back.
        """
        token = "1"
        seen: set[str] = set()
        while True:
            page_body = dict(body)
            page_body["pagination"] = {"pageSize": PAGE_SIZE, "pageToken": token}
            data = self.post(path, page_body)
            items = data.get(key) or []
            for it in items:
                yield it
            seen.add(token)
            nxt = str(data.get("nextPageToken") or "")
            if not nxt and len(items) >= PAGE_SIZE:
                nxt = str(int(token) + 1)
            if not nxt or not items or nxt in seen:
                break
            token = nxt

    # -- entities ------------------------------------------------------------

    def list_companies(self) -> list[dict]:
        # Mirrors MoeGo's own example (pageSize 100, pageToken "1"); only used for display names,
        # so fail fast rather than burning a minute of retries.
        data = self.post("/v1/companies:list", {"pagination": {"pageSize": 100, "pageToken": "1"}}, max_retries=2)
        return data.get("companies") or []

    def list_businesses(self, company_id: str) -> list[dict]:
        return list(self.paginate("/v1/businesses:list", {"companyId": company_id}, "businesses"))

    def list_staff(self, company_id: str) -> list[dict]:
        return list(self.paginate("/v1/staffs:list", {"companyId": company_id}, "staffs"))

    def list_services(self, company_id: str) -> list[dict]:
        return list(self.paginate(f"/v1/setting/companies/{company_id}/services:list",
                                  {"companyId": company_id}, "services"))

    def list_customers(self, company_id: str, updated: Optional[dict] = None) -> Iterator[dict]:
        body: dict[str, Any] = {"companyId": company_id}
        if updated:
            body["filter"] = {"lastUpdatedTime": updated}
        return self.paginate("/v1/customers:list", body, "customers")

    def list_pets(self, company_id: str, updated: Optional[dict] = None) -> Iterator[dict]:
        body: dict[str, Any] = {"companyId": company_id}
        if updated:
            body["filter"] = {"lastUpdatedTime": updated}
        return self.paginate("/v1/pets:list", body, "pets")

    def list_appointments(self, company_id: str, business_ids: list[str],
                          filt: Optional[dict] = None) -> Iterator[dict]:
        body: dict[str, Any] = {"companyId": company_id, "businessIds": business_ids}
        if filt:
            body["filter"] = filt
        return self.paginate("/v1/appointments:list", body, "appointments")

    def list_orders(self, company_id: str, business_ids: list[str],
                    updated: Optional[dict] = None) -> Iterator[dict]:
        body: dict[str, Any] = {"companyId": company_id, "businessIds": business_ids}
        if updated:
            body["filter"] = {"lastUpdatedTime": updated}
        return self.paginate("/v1/orders:list", body, "orders")

    def list_order_line_items(self, company_id: str, order_ids: list[str]) -> dict[str, list]:
        """Returns {'orderLineItems': [...], 'orderLineItemTaxes': [...]} for <=100 order ids."""
        out: dict[str, list] = {"orderLineItems": [], "orderLineItemTaxes": []}
        for i in range(0, len(order_ids), 100):
            chunk = order_ids[i:i + 100]
            data = self.post("/v1/orders/items:list",
                             {"companyId": company_id, "filter": {"orderIds": chunk}})
            for k in out:
                out[k].extend(data.get(k) or [])
        return out

    def list_payments(self, company_id: str, order_ids: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(order_ids), 100):
            chunk = order_ids[i:i + 100]
            out.extend(self.paginate("/v1/payments:list",
                                     {"companyId": company_id, "filter": {"orderIds": chunk}},
                                     "payments"))
        return out
