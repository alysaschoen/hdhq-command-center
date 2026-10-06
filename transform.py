"""Flatten MoeGo API objects into the tabular rows that land in Zoho Analytics.

Every row carries ``location_key`` / ``location_name`` / ``business_id`` /
``company_id`` so the dashboard can slice by location without joins, plus the
``Locations`` table for state/region roll-ups.

Conventions
-----------
* Timestamps are emitted as ``YYYY-MM-DD HH:MM:SS`` in UTC (``*_utc``) and, for
  the appointment start, also as the location-local date/time so "appointments
  on Tuesday" means Tuesday where the van was.
* Money (``{"units": "45", "nanos": 500000000}``) becomes a float in dollars.
* Nested lists are flattened into child tables (AppointmentServices,
  OrderLineItems, Payments) rather than JSON-in-a-cell.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

TS_FMT = "%Y-%m-%d %H:%M:%S"


# --- scalar helpers ----------------------------------------------------------

def money(m: Optional[dict]) -> Optional[float]:
    if not m:
        return None
    try:
        return round(float(m.get("units") or 0) + float(m.get("nanos") or 0) / 1e9, 2)
    except (TypeError, ValueError):
        return None


def parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        if "." in s2:  # trim >6 fractional digits which fromisoformat rejects
            head, tail = s2.split(".", 1)
            frac = "".join(ch for ch in tail if ch.isdigit())[:6]
            rest = tail[len("".join(ch for ch in tail if ch.isdigit())):]
            s2 = f"{head}.{frac}{rest}"
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def ts_utc(s: Optional[str]) -> Optional[str]:
    dt = parse_ts(s)
    return dt.astimezone(timezone.utc).strftime(TS_FMT) if dt else None


def ts_local(s: Optional[str], tz: Optional[str]) -> Optional[str]:
    dt = parse_ts(s)
    if not dt:
        return None
    if tz:
        try:
            dt = dt.astimezone(ZoneInfo(tz))
        except Exception:
            dt = dt.astimezone(timezone.utc)
    return dt.strftime(TS_FMT)


def date_local(s: Optional[str], tz: Optional[str]) -> Optional[str]:
    v = ts_local(s, tz)
    return v[:10] if v else None


def minutes_between(a: Optional[str], b: Optional[str]) -> Optional[int]:
    da, db = parse_ts(a), parse_ts(b)
    if da and db:
        return int((db - da).total_seconds() // 60)
    return None


def _b(v: Any) -> str:
    """Zoho-friendly boolean literal."""
    return "true" if v else "false"


def _date(d: Optional[dict]) -> Optional[str]:
    if not d or not d.get("year"):
        return None
    return f"{int(d['year']):04d}-{int(d.get('month') or 1):02d}-{int(d.get('day') or 1):02d}"


def _addr(a: Optional[dict], prefix: str = "") -> dict[str, Any]:
    a = a or {}
    return {
        f"{prefix}address1": a.get("address1"),
        f"{prefix}city": a.get("city"),
        f"{prefix}state": a.get("state"),
        f"{prefix}postal_code": a.get("postalCode"),
        f"{prefix}lat": (a.get("coordinate") or {}).get("latitude"),
        f"{prefix}lng": (a.get("coordinate") or {}).get("longitude"),
    }


def _loc(loc: dict) -> dict[str, Any]:
    return {
        "location_key": loc["location_key"],
        "location_name": loc["location_name"],
        "company_id": loc["company_id"],
        "business_id": loc["business_id"],
    }


# --- row builders --------------------------------------------------------------

def location_row(loc: dict, business: Optional[dict], company: Optional[dict]) -> dict:
    b = business or {}
    return {
        "id": loc["location_key"],
        **_loc(loc),
        **_addr(b.get("address")),
        "state": loc.get("state"),          # keep the 2-letter code from locations.csv
        "region": loc.get("region"),
        "timezone": loc.get("timezone"),
        "moego_business_name": b.get("name"),
        "moego_company_name": (company or {}).get("name"),
        "moego_timezone": ((company or {}).get("timezone") or {}).get("id"),
        "business_phone": b.get("businessPhone") or b.get("phone"),
        "business_email": b.get("email"),
    }


def appointment_rows(appt: dict, loc: dict) -> tuple[dict, list[dict]]:
    """Return (appointment_row, [service_rows])."""
    tz = loc.get("timezone")
    dur = appt.get("duration") or {}
    psds = appt.get("petServiceDetails") or []
    pet_ids = [str((p.get("pet") or {}).get("id") or "") for p in psds]
    service_names = [s.get("name") for p in psds for s in (p.get("serviceDetails") or []) if s.get("name")]
    staff_ids = sorted({sid for p in psds for s in (p.get("serviceDetails") or []) for sid in (s.get("staffIds") or [])})
    services_total = sum((money(s.get("price")) or 0.0) for p in psds for s in (p.get("serviceDetails") or []))

    row = {
        "id": appt.get("id"),
        **_loc(loc),
        "customer_id": appt.get("customerId"),
        "order_id": appt.get("orderId"),
        "status": appt.get("status"),
        "payment_status": appt.get("paymentStatus"),
        "no_show": _b(appt.get("noShow")),
        "is_deleted": _b(appt.get("isDeleted")),
        "start_utc": ts_utc(dur.get("startTime")),
        "end_utc": ts_utc(dur.get("endTime")),
        "start_local": ts_local(dur.get("startTime"), tz),
        "end_local": ts_local(dur.get("endTime"), tz),
        "appointment_date": date_local(dur.get("startTime"), tz),
        "scheduled_minutes": minutes_between(dur.get("startTime"), dur.get("endTime")),
        "check_in_utc": ts_utc(appt.get("checkInTime")),
        "check_out_utc": ts_utc(appt.get("checkOutTime")),
        "actual_minutes": minutes_between(appt.get("checkInTime"), appt.get("checkOutTime")),
        "confirmed_utc": ts_utc(appt.get("confirmedTime")),
        "ready_utc": ts_utc(appt.get("readyTime")),
        "canceled_utc": ts_utc(appt.get("canceledTime")),
        "created_utc": ts_utc(appt.get("createdTime")),
        "last_updated_utc": ts_utc(appt.get("lastUpdatedTime")),
        "created_by_staff_id": appt.get("createdBy"),
        "total_amount": money(appt.get("totalAmount")),
        "paid_amount": money(appt.get("paidAmount")),
        "refund_amount": money(appt.get("refundAmount")),
        "no_show_fee": money(appt.get("noShowFee")),
        "services_list_price": round(services_total, 2),
        "pet_count": len(psds),
        "service_count": len(service_names),
        "pet_ids": ",".join(pid for pid in pet_ids if pid),
        "staff_ids": ",".join(staff_ids),
        "primary_staff_id": staff_ids[0] if staff_ids else None,
        "service_names": " | ".join(service_names),
        "booking_request_id": appt.get("bookingRequestId"),
        "ticket_comment": (appt.get("ticketComment") or "")[:500],
        **_addr(appt.get("address"), "service_"),
    }

    svc_rows: list[dict] = []
    for p in psds:
        pet = p.get("pet") or {}
        evals = p.get("evaluationDetails") or []
        for s in p.get("serviceDetails") or []:
            sd = s.get("duration") or {}
            sids = s.get("staffIds") or []
            svc_rows.append({
                "id": s.get("petServiceDetailId") or f"{appt.get('id')}:{pet.get('id')}:{s.get('id')}",
                "appointment_id": appt.get("id"),
                **_loc(loc),
                "customer_id": appt.get("customerId"),
                "appointment_date": row["appointment_date"],
                "appointment_status": appt.get("status"),
                "pet_id": pet.get("id"),
                "pet_name": pet.get("name"),
                "pet_breed": pet.get("breed"),
                "pet_type": pet.get("type"),
                "service_id": s.get("id"),
                "service_name": s.get("name"),
                "service_item_type": s.get("serviceItemType"),
                "service_type": s.get("serviceType"),
                "service_category": s.get("category"),
                "price": money(s.get("price")),
                "service_minutes": s.get("serviceTime"),
                "start_utc": ts_utc(sd.get("startTime")),
                "end_utc": ts_utc(sd.get("endTime")),
                "staff_id": sids[0] if sids else None,
                "staff_ids": ",".join(sids),
                "evaluation_status": (evals[0].get("status") if evals else None),
            })
    return row, svc_rows


def customer_row(c: dict, loc: dict) -> dict:
    addrs = c.get("address") or []
    primary = next((a for a in addrs if a.get("type") == "PRIMARY"), addrs[0] if addrs else None)
    tags = [t.get("name") for t in (c.get("tags") or []) if t.get("name")]
    ref = c.get("referralSource") or {}
    return {
        "id": c.get("id"),
        **_loc(loc),
        "preferred_business_id": c.get("preferredBusinessId"),
        "first_name": c.get("firstName"),
        "last_name": c.get("lastName"),
        "phone": c.get("phone"),
        "email": c.get("email"),
        "status": c.get("status"),
        "source": c.get("source"),
        "referral_source": ref.get("name") if isinstance(ref, dict) else ref,
        "tags": " | ".join(tags),
        "last_appointment_utc": ts_utc(c.get("lastAppointmentDate")),
        "next_appointment_utc": ts_utc(c.get("nextAppointmentDate")),
        "created_utc": ts_utc(c.get("createdTime")),
        "created_date": (ts_utc(c.get("createdTime")) or "")[:10] or None,
        "last_updated_utc": ts_utc(c.get("lastUpdatedTime")),
        "is_deleted": _b(c.get("deleted")),
        **_addr(primary),
    }


def pet_row(p: dict, loc: dict) -> dict:
    w = p.get("weight") or {}
    codes = [c.get("abbreviation") or c.get("description") for c in (p.get("petCodes") or [])]
    return {
        "id": p.get("id"),
        **_loc(loc),
        "customer_id": p.get("customerId"),
        "name": p.get("name"),
        "type": p.get("type"),
        "breed": p.get("breed"),
        "gender": p.get("gender"),
        "status": p.get("status"),
        "birthday": _date(p.get("birthday")),
        "weight_value": w.get("value"),
        "weight_unit": w.get("unit"),
        "fixed": p.get("fixed"),
        "coat": p.get("coat"),
        "behavior": p.get("behavior"),
        "evaluation_status": p.get("evaluationStatus"),
        "pet_codes": " | ".join(c for c in codes if c),
        "created_utc": ts_utc(p.get("createdTime")),
        "last_updated_utc": ts_utc(p.get("lastUpdatedTime")),
        "is_deleted": _b(p.get("deleted")),
    }


def staff_row(s: dict, loc: dict) -> dict:
    role = s.get("role") or {}
    return {
        "id": s.get("id"),
        **_loc(loc),
        "first_name": s.get("firstName"),
        "last_name": s.get("lastName"),
        "full_name": " ".join(x for x in [s.get("firstName"), s.get("lastName")] if x),
        "email": s.get("email"),
        "phone": s.get("phone"),
        "role": role.get("name") if isinstance(role, dict) else role,
        "hired_utc": ts_utc(s.get("hiredTime")),
        "working_business_ids": ",".join(s.get("workingBusinessIds") or []),
        "mobile_van_id": s.get("mobileVanId"),
        "is_deleted": _b(s.get("deleted")),
    }


def service_row(s: dict, loc: dict) -> dict:
    return {
        "id": s.get("id"),
        **_loc(loc),
        "name": s.get("name"),
        "service_item_type": s.get("serviceItemType"),
        "service_type": s.get("serviceType"),
        "category": s.get("category"),
        "price": money(s.get("price")),
        "service_minutes": s.get("serviceTime"),
        "available_all_business": _b(s.get("availableAllBusiness")),
    }


def order_row(o: dict, loc: dict) -> dict:
    tz = loc.get("timezone")
    return {
        "id": o.get("id"),
        **_loc(loc),
        "customer_id": o.get("customerId"),
        "status": o.get("status"),
        "title": o.get("title"),
        "sub_total": money(o.get("subTotalAmount")),
        "discount_amount": money(o.get("discountAmount")),
        "tax_amount": money(o.get("taxAmount")),
        "tips_amount": money(o.get("tipsAmount")),
        "extra_fee_amount": money(o.get("extraFeeAmount")),
        "total_amount": money(o.get("totalAmount")),
        "paid_amount": money(o.get("paidAmount")),
        "remain_amount": money(o.get("remainAmount")),
        "refunded_amount": money(o.get("refundedAmount")),
        "sales_utc": ts_utc(o.get("salesDatetime")),
        "sales_date": date_local(o.get("salesDatetime"), tz),
        "completed_utc": ts_utc(o.get("completedTime")),
        "created_utc": ts_utc(o.get("createdTime")),
        "last_updated_utc": ts_utc(o.get("lastUpdatedTime")),
        "created_by_staff_id": o.get("createdBy"),
    }


def order_line_item_row(li: dict, loc: dict, taxes_by_item: dict[str, float]) -> dict:
    qty = li.get("quantity") or 1
    unit = money(li.get("unitPrice"))
    return {
        "id": li.get("id"),
        "order_id": li.get("orderId"),
        **_loc(loc),
        "object_type": li.get("objectType"),
        "object_id": li.get("objectId"),
        "name": li.get("name"),
        "quantity": qty,
        "unit_price": unit,
        "line_total": round(unit * qty, 2) if unit is not None else None,
        "paid_amount": money(li.get("paidAmount")),
        "tax_amount": taxes_by_item.get(str(li.get("id"))),
        "created_utc": ts_utc(li.get("createdTime")),
    }


def payment_row(p: dict, loc: dict) -> dict:
    tz = loc.get("timezone")
    return {
        "id": p.get("id"),
        **_loc(loc),
        "order_id": p.get("orderId"),
        "customer_id": p.get("customerId"),
        "customer_name": p.get("customerName"),
        "method": p.get("method"),
        "status": p.get("status"),
        "module": p.get("module"),
        "amount": money(p.get("amount")),
        "processing_fee": money(p.get("processingFee")),
        "refund_amount": money(p.get("refundAmount")),
        "refund_count": len(p.get("refunds") or []),
        "created_utc": ts_utc(p.get("createdTime")),
        "payment_date": date_local(p.get("createdTime"), tz),
        "last_updated_utc": ts_utc(p.get("lastUpdatedTime")),
    }
