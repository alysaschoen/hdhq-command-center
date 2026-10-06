"""Metric layer for the HDHQ dashboard.

Pure pandas, no Streamlit — so it can be unit-tested and reused.
Reads the CSV snapshots the nightly sync writes to ../data/ (same rows that
land in Zoho Analytics), and reproduces the QT_* query-table logic.

Revenue definition (leadership): cash basis = payments collected - refunds,
status in (PAID, COMPLETED).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATE_MIN, DATE_MAX = pd.Timestamp("2022-01-01"), pd.Timestamp("2027-12-31")


# --- loading -------------------------------------------------------------------

import os
WAREHOUSE = Path(os.getenv("HDHQ_WAREHOUSE") or DATA_DIR / "hdhq.sqlite")


def _read(name: str, parse_dates: list[str] = ()) -> pd.DataFrame:
    if WAREHOUSE.exists():
        import sqlite3
        con = sqlite3.connect(WAREHOUSE)
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{name}"', con)
        except Exception:
            df = pd.DataFrame()
        finally:
            con.close()
    else:
        path = DATA_DIR / f"{name}.csv"
        if not path.exists() or path.stat().st_size == 0:
            return pd.DataFrame()
        df = pd.read_csv(path, low_memory=False)
    # SQLite/CSV give us strings. Force-convert known numeric columns, then date columns.
    NUM_SUFFIX = ("_amount", "_fee", "_price", "_total", "_minutes", "_count", "_value", "_lat", "_lng",
                  "_rate", "_s", "_hour")
    NUM_NAMES = {"price", "sub_total", "lat", "lng", "quantity", "weight_value", "amount", "service_minutes",
                 "scheduled_minutes", "actual_minutes", "pet_count", "service_count", "locations"}
    for c in df.columns:
        if c.endswith(NUM_SUFFIX) or c in NUM_NAMES or c.startswith("rows_"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    parse_dates = set(parse_dates) | {c for c in df.columns if c.endswith(("_utc", "_local", "_date")) or c in ("birthday", "day")}
    for c in parse_dates:
        if c in df:
            df[c] = pd.to_datetime(df[c].astype("string").str.strip().replace("", pd.NA), errors="coerce")
    for c in df.columns:
        if c in ("is_deleted", "no_show", "available_all_business"):
            df[c] = df[c].astype(str).str.lower().eq("true")
    return df


@dataclass
class Data:
    locations: pd.DataFrame
    appointments: pd.DataFrame
    services: pd.DataFrame      # AppointmentServices
    customers: pd.DataFrame
    payments: pd.DataFrame
    staff: pd.DataFrame
    orders: pd.DataFrame

    @property
    def location_names(self) -> dict[str, str]:
        return dict(zip(self.locations.location_key, self.locations.location_name))

    @property
    def staff_names(self) -> dict[str, str]:
        st = self.staff
        names = st.get("full_name")
        if names is None or names.isna().all():
            names = (st.first_name.fillna("") + " " + st.last_name.fillna("")).str.strip()
        return dict(zip(st.id, names))


def load() -> Data:
    appts = _read("Appointments", ["start_utc", "start_local", "appointment_date", "check_in_utc",
                                   "check_out_utc", "created_utc", "last_updated_utc"])
    appts = appts[(~appts.is_deleted) & appts.appointment_date.between(DATE_MIN, DATE_MAX)]
    svc = _read("AppointmentServices", ["appointment_date", "start_utc"])
    svc = svc[svc.appointment_date.between(DATE_MIN, DATE_MAX)]
    cust = _read("Customers", ["created_date", "last_appointment_utc", "next_appointment_utc"])
    cust = cust[~cust.is_deleted]
    pay = _read("Payments", ["payment_date", "created_utc"])
    pay = pay[pay.status.isin(["PAID", "COMPLETED"]) & pay.payment_date.between(DATE_MIN, DATE_MAX)]
    pay["net"] = pay.amount.fillna(0) - pay.refund_amount.fillna(0)
    locs = _read("Locations")
    staff = _read("Staff")
    orders = _read("Orders", ["sales_date", "completed_utc"])
    orders = orders[orders.status.isin(["COMPLETED", "PROCESSING", "CREATED"]) & orders.sales_date.between(DATE_MIN, DATE_MAX)]
    return Data(locs, appts, svc, cust, pay, staff, orders)


# --- filters -----------------------------------------------------------------------

def _num(df: pd.DataFrame, skip=()) -> pd.DataFrame:
    """pandas 3 turns empty/NaN-only columns into str after fillna(0); force numeric."""
    for c in df.columns:
        if c not in skip:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


def in_range(df: pd.DataFrame, col: str, start, end) -> pd.DataFrame:
    return df[(df[col] >= pd.Timestamp(start)) & (df[col] <= pd.Timestamp(end))]


def for_locations(df: pd.DataFrame, keys: list[str] | None) -> pd.DataFrame:
    return df if not keys else df[df.location_key.isin(keys)]


# --- KPIs -------------------------------------------------------------------------

def kpis(d: Data, start, end, keys=None) -> dict:
    a = for_locations(in_range(d.appointments, "appointment_date", start, end), keys)
    p = for_locations(in_range(d.payments, "payment_date", start, end), keys)
    c = for_locations(in_range(d.customers, "created_date", start, end), keys)
    finished = a[a.status == "FINISHED"]
    n = len(a)
    return {
        "net_revenue": float(p.net.sum()),
        "appointments": n,
        "finished": len(finished),
        "cancel_rate": float((a.status == "CANCELED").mean()) if n else 0.0,
        "no_show_rate": float(a.no_show.mean()) if n else 0.0,
        "avg_ticket": float(finished.total_amount.mean()) if len(finished) else 0.0,
        "new_customers": len(c),
        "unique_customers": int(finished.customer_id.nunique()),
        "pets_served": int(finished.pet_count.sum()) if len(finished) else 0,
    }


def prior_period(start, end):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    span = end - start + pd.Timedelta(days=1)
    return start - span, end - span


# --- time series --------------------------------------------------------------------

def monthly_revenue(d: Data, start, end, keys=None, by_location=False) -> pd.DataFrame:
    p = for_locations(in_range(d.payments, "payment_date", start, end), keys).copy()
    p["month"] = p.payment_date.dt.to_period("M").dt.to_timestamp()
    grp = ["month", "location_name"] if by_location else ["month"]
    return p.groupby(grp, as_index=False).agg(net_revenue=("net", "sum"), payments=("id", "count"))


def monthly_appointments(d: Data, start, end, keys=None) -> pd.DataFrame:
    a = for_locations(in_range(d.appointments, "appointment_date", start, end), keys).copy()
    a["month"] = a.appointment_date.dt.to_period("M").dt.to_timestamp()
    out = a.groupby("month").agg(
        appointments=("id", "count"),
        finished=("status", lambda s: (s == "FINISHED").sum()),
        canceled=("status", lambda s: (s == "CANCELED").sum()),
        no_shows=("no_show", "sum"),
        unique_customers=("customer_id", "nunique"),
    ).reset_index()
    out["cancel_rate"] = out.canceled / out.appointments.replace(0, np.nan)
    out["no_show_rate"] = out.no_shows / out.appointments.replace(0, np.nan)
    return out


def revenue_by_location(d: Data, start, end, keys=None) -> pd.DataFrame:
    p = for_locations(in_range(d.payments, "payment_date", start, end), keys)
    out = p.groupby(["location_key", "location_name"], as_index=False).agg(
        net_revenue=("net", "sum"), payments=("id", "count"), customers=("customer_id", "nunique"))
    return out.sort_values("net_revenue", ascending=False)


def service_mix(d: Data, start, end, keys=None, top=10) -> pd.DataFrame:
    s = for_locations(in_range(d.services, "appointment_date", start, end), keys)
    s = s[s.appointment_status == "FINISHED"]
    out = s.groupby("service_name", as_index=False).agg(value=("price", "sum"), count=("id", "count"))
    out = out.sort_values("value", ascending=False)
    if len(out) > top:
        other = pd.DataFrame({"service_name": ["Other"], "value": [out.value[top:].sum()], "count": [out["count"][top:].sum()]})
        out = pd.concat([out.head(top), other], ignore_index=True)
    return out


# --- scorecard ------------------------------------------------------------------------

def scorecard(d: Data, start, end, keys=None) -> pd.DataFrame:
    """One row per location: current period vs prior period of equal length."""
    pstart, pend = prior_period(start, end)

    def period(s, e):
        a = for_locations(in_range(d.appointments, "appointment_date", s, e), keys)
        p = for_locations(in_range(d.payments, "payment_date", s, e), keys)
        c = for_locations(in_range(d.customers, "created_date", s, e), keys)
        g = a.groupby("location_key").agg(
            appointments=("id", "count"),
            finished=("status", lambda x: (x == "FINISHED").sum()),
            canceled=("status", lambda x: (x == "CANCELED").sum()),
            no_shows=("no_show", "sum"),
            customers=("customer_id", "nunique"),
            groomers=("primary_staff_id", "nunique"),
        )
        g["net_revenue"] = p.groupby("location_key").net.sum()
        g["new_customers"] = c.groupby("location_key").id.count()
        g = _num(g.fillna(0))
        g["cancel_rate"] = g.canceled / g.appointments.replace(0, np.nan)
        g["no_show_rate"] = g.no_shows / g.appointments.replace(0, np.nan)
        g["avg_ticket"] = g.net_revenue / g.finished.replace(0, np.nan)
        return g

    cur, prev = period(start, end), period(pstart, pend)
    out = _num(cur.join(prev, rsuffix="_prev", how="left").fillna(0))
    for m in ("net_revenue", "finished", "new_customers", "appointments"):
        out[f"{m}_chg"] = (out[m] - out[f"{m}_prev"]) / out[f"{m}_prev"].replace(0, np.nan)
    out.insert(0, "location_name", out.index.map(d.location_names))
    return out.reset_index().sort_values("net_revenue", ascending=False)


# --- customers ----------------------------------------------------------------------------

def new_customers_monthly(d: Data, start, end, keys=None) -> pd.DataFrame:
    c = for_locations(in_range(d.customers, "created_date", start, end), keys).copy()
    c["month"] = c.created_date.dt.to_period("M").dt.to_timestamp()
    return c.groupby("month", as_index=False).agg(new_customers=("id", "count"))


def activity_buckets(d: Data, keys=None, today=None) -> pd.DataFrame:
    """Customer activity derived from the Appointments table (MoeGo's own
    last/next-appointment fields on the customer record are sparsely populated)."""
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    c = for_locations(d.customers, keys).copy()
    if "full_name" not in c or c.full_name.isna().all():
        c["full_name"] = (c.first_name.fillna("") + " " + c.last_name.fillna("")).str.strip()
    a = d.appointments
    last = a[(a.status == "FINISHED") & (a.appointment_date <= today)].groupby("customer_id").appointment_date.max()
    nxt = a[a.status.isin(["CONFIRMED", "UNCONFIRMED"]) & (a.appointment_date > today)].groupby("customer_id").appointment_date.min()
    c["last_appointment_utc"] = c.id.map(last)
    c["next_appointment_utc"] = c.id.map(nxt)
    days = (today - c.last_appointment_utc).dt.days
    c["activity_bucket"] = np.select(
        [c.next_appointment_utc > today, c.last_appointment_utc.isna(), days <= 60, days <= 120],
        ["Booked", "Never visited", "Active (0-60d)", "Cooling (61-120d)"], default="Lapsed (120d+)")
    c["days_since_last_visit"] = days
    return c


BUCKET_ORDER = ["Booked", "Active (0-60d)", "Cooling (61-120d)", "Lapsed (120d+)", "Never visited"]


def bucket_by_location(c: pd.DataFrame) -> pd.DataFrame:
    out = c.groupby(["location_name", "activity_bucket"]).size().unstack(fill_value=0)
    out = out.reindex(columns=[b for b in BUCKET_ORDER if b in out.columns])
    out["total"] = out.sum(axis=1)
    return out.sort_values("total", ascending=False)


def lapsed_customers(c: pd.DataFrame, min_visits_df: pd.DataFrame | None = None) -> pd.DataFrame:
    l = c[c.activity_bucket == "Lapsed (120d+)"]
    cols = ["location_name", "full_name", "phone", "email", "last_appointment_utc", "days_since_last_visit", "source"]
    return l[cols].sort_values("days_since_last_visit")


def visit_frequency(d: Data, keys=None) -> pd.DataFrame:
    a = for_locations(d.appointments[d.appointments.status == "FINISHED"], keys)
    v = a.groupby("customer_id").size()
    bucket = pd.cut(v, [0, 1, 3, 8, np.inf], labels=["One-time", "2-3 visits", "4-8 visits", "9+ visits"])
    return bucket.value_counts().reindex(["One-time", "2-3 visits", "4-8 visits", "9+ visits"]).rename_axis("frequency").reset_index(name="customers")


def referral_sources(d: Data, keys=None, top=10) -> pd.DataFrame:
    c = for_locations(d.customers, keys).copy()
    c["source"] = c.referral_source.fillna("").replace("", np.nan).fillna(c.source).fillna("Unknown").replace("", "Unknown")
    out = c.groupby("source", as_index=False).size().rename(columns={"size": "customers"}).sort_values("customers", ascending=False)
    return out.head(top)


# --- groomers --------------------------------------------------------------------------------

def groomer_leaderboard(d: Data, start, end, keys=None) -> pd.DataFrame:
    s = for_locations(in_range(d.services, "appointment_date", start, end), keys).copy()
    s["staff_name"] = s.staff_id.map(d.staff_names).fillna(s.staff_id)
    s = s[s.staff_name.fillna("").ne("")]
    fin = s[s.appointment_status == "FINISHED"]
    out = fin.groupby(["staff_name", "location_name"], as_index=False).agg(
        service_value=("price", "sum"), services=("id", "count"),
        appointments=("appointment_id", "nunique"), pets=("pet_id", "nunique"),
        minutes=("service_minutes", "sum"))
    out["value_per_hour"] = out.service_value / (out.minutes.replace(0, np.nan) / 60)
    return out.sort_values("service_value", ascending=False)


def groomer_timing(d: Data, start, end, keys=None) -> pd.DataFrame:
    a = for_locations(in_range(d.appointments, "appointment_date", start, end), keys).copy()
    a["primary_staff_name"] = a.primary_staff_id.map(d.staff_names).fillna(a.primary_staff_id)
    a = a[(a.status == "FINISHED") & a.primary_staff_name.fillna("").ne("") & a.actual_minutes.gt(0)]
    out = a.groupby("primary_staff_name", as_index=False).agg(
        appointments=("id", "count"), avg_scheduled=("scheduled_minutes", "mean"), avg_actual=("actual_minutes", "mean"))
    out["variance_min"] = out.avg_actual - out.avg_scheduled
    return out[out.appointments >= 10].sort_values("appointments", ascending=False)


def weekday_hour_heat(d: Data, start, end, keys=None) -> pd.DataFrame:
    a = for_locations(in_range(d.appointments, "appointment_date", start, end), keys)
    a = a[a.status == "FINISHED"].copy()
    a["weekday"] = a.start_local.dt.day_name()
    a["hour"] = a.start_local.dt.hour
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    heat = a.groupby(["weekday", "hour"]).size().unstack(fill_value=0).reindex(days).fillna(0)
    return heat.loc[:, (heat.columns >= 6) & (heat.columns <= 20)]


# =====================================================================================
# v2: pulse (daily/weekly), rebooking, cohorts, cadence, lost revenue, lead time,
#     utilization, tips/discounts, momentum, LTV by source
# =====================================================================================

def _grain(series: pd.Series, grain: str) -> pd.Series:
    if grain == "day":
        return series.dt.normalize()
    if grain == "week":
        return series.dt.to_period("W-SUN").dt.start_time
    return series.dt.to_period("M").dt.to_timestamp()


def pulse(d: Data, start, end, keys=None, grain="day") -> pd.DataFrame:
    """Revenue + appointment outcomes per day/week/month, with 7-period moving avg."""
    a = for_locations(in_range(d.appointments, "appointment_date", start, end), keys).copy()
    p = for_locations(in_range(d.payments, "payment_date", start, end), keys).copy()
    a["bucket"] = _grain(a.appointment_date, grain)
    p["bucket"] = _grain(p.payment_date, grain)
    out = a.groupby("bucket").agg(
        appointments=("id", "count"),
        finished=("status", lambda s: (s == "FINISHED").sum()),
        canceled=("status", lambda s: (s == "CANCELED").sum()),
        no_shows=("no_show", "sum"),
        lost_value=("total_amount", lambda s: s[(a.loc[s.index, "status"] == "CANCELED") | a.loc[s.index, "no_show"]].sum()),
        finished_value=("total_amount", lambda s: s[a.loc[s.index, "status"] == "FINISHED"].sum()),
        pets=("pet_count", "sum"),
    )
    out["net_revenue"] = p.groupby("bucket").net.sum()
    out = _num(out.fillna(0)).reset_index()
    out["cancel_rate"] = out.canceled / out.appointments.replace(0, np.nan)
    w = 7 if grain == "day" else 4
    out["revenue_ma"] = out.net_revenue.rolling(w, min_periods=1).mean()
    out["finished_ma"] = out.finished.rolling(w, min_periods=1).mean()
    return out


def pulse_by_location(d: Data, start, end, keys=None, grain="week") -> pd.DataFrame:
    a = for_locations(in_range(d.appointments, "appointment_date", start, end), keys).copy()
    p = for_locations(in_range(d.payments, "payment_date", start, end), keys).copy()
    a["bucket"] = _grain(a.appointment_date, grain)
    p["bucket"] = _grain(p.payment_date, grain)
    fin = a[a.status == "FINISHED"].groupby(["location_name", "bucket"]).size().rename("finished")
    rev = p.groupby(["location_name", "bucket"]).net.sum().rename("net_revenue")
    return pd.concat([fin, rev], axis=1).fillna(0).reset_index()


def momentum(d: Data, keys=None, weeks=4, today=None) -> pd.DataFrame:
    """Last N weeks vs the N weeks before, per location."""
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    cur_s, prev_s = today - pd.Timedelta(weeks=weeks), today - pd.Timedelta(weeks=2 * weeks)
    p = for_locations(d.payments, keys)
    a = for_locations(d.appointments[d.appointments.status == "FINISHED"], keys)
    cur = p[(p.payment_date > cur_s) & (p.payment_date <= today)].groupby("location_name").net.sum()
    prev = p[(p.payment_date > prev_s) & (p.payment_date <= cur_s)].groupby("location_name").net.sum()
    fc = a[(a.appointment_date > cur_s) & (a.appointment_date <= today)].groupby("location_name").size()
    fp = a[(a.appointment_date > prev_s) & (a.appointment_date <= cur_s)].groupby("location_name").size()
    out = _num(pd.DataFrame({"revenue_cur": cur, "revenue_prev": prev, "finished_cur": fc, "finished_prev": fp}).fillna(0))
    out["revenue_chg"] = (out.revenue_cur - out.revenue_prev) / out.revenue_prev.replace(0, np.nan)
    out["finished_chg"] = (out.finished_cur - out.finished_prev) / out.finished_prev.replace(0, np.nan)
    return out.reset_index().rename(columns={"index": "location_name"}).sort_values("revenue_chg", ascending=False)


def _finished_sorted(d: Data, keys=None) -> pd.DataFrame:
    a = for_locations(d.appointments, keys)
    a = a[a.status == "FINISHED"].sort_values(["customer_id", "appointment_date"])
    return a


def rebooking(d: Data, start, end, keys=None, within_days=7) -> pd.DataFrame:
    """For each finished appointment in the period: was the customer's next appointment
    *created* within `within_days` of this visit (i.e. rebooked at checkout / shortly after)?"""
    a = for_locations(d.appointments, keys)
    a = a[a.status != "CANCELED"].copy()
    a["created_date"] = a.created_utc.dt.normalize()
    fin = a[(a.status == "FINISHED")].copy()
    fin = in_range(fin, "appointment_date", start, end)
    nxt = a[["customer_id", "appointment_date", "created_date", "id"]].rename(
        columns={"appointment_date": "next_date", "created_date": "next_created", "id": "next_id"})
    m_ = fin.merge(nxt, on="customer_id", how="left")
    m_ = m_[(m_.next_date > m_.appointment_date)]
    # earliest future appointment per finished visit
    m_ = m_.sort_values("next_date").groupby("id", as_index=False).first()
    fin = fin.merge(m_[["id", "next_date", "next_created"]], on="id", how="left")
    fin["rebooked"] = (fin.next_created - fin.appointment_date).dt.days.between(-30, within_days)  # booked at/just after visit
    fin["has_next"] = fin.next_date.notna()
    fin["days_to_next"] = (fin.next_date - fin.appointment_date).dt.days
    return fin


def rebooking_summary(fin: pd.DataFrame, by: str = "location_name") -> pd.DataFrame:
    g = fin.groupby(by).agg(visits=("id", "count"), rebooked=("rebooked", "sum"), has_next=("has_next", "sum"),
                            median_days_to_next=("days_to_next", "median"))
    g["rebook_rate"] = g.rebooked / g.visits
    g["any_next_rate"] = g.has_next / g.visits
    return g.sort_values("rebook_rate", ascending=False).reset_index()


def cadence(d: Data, keys=None, today=None) -> pd.DataFrame:
    """Per customer: visits, median days between visits, days since last, overdue flag,
    avg ticket — the win-back list that respects each customer's own rhythm."""
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    a = _finished_sorted(d, keys)
    a = a[a.appointment_date <= today]
    gaps = a.groupby("customer_id").appointment_date.diff().dt.days
    a = a.assign(gap=gaps)
    if "customer_name" not in a or a.customer_name.isna().all():
        c0 = d.customers.set_index("id")
        a = a.assign(customer_name=a.customer_id.map((c0.first_name.fillna("") + " " + c0.last_name.fillna("")).str.strip()))
    g = a.groupby("customer_id").agg(location_name=("location_name", "last"), customer_name=("customer_name", "last"),
                                     visits=("id", "count"), last_visit=("appointment_date", "max"),
                                     first_visit=("appointment_date", "min"), median_gap=("gap", "median"),
                                     avg_ticket=("total_amount", "mean"), lifetime_value=("total_amount", "sum"))
    future = d.appointments[(d.appointments.appointment_date > today) & d.appointments.status.isin(["CONFIRMED", "UNCONFIRMED"])]
    g["booked"] = g.index.isin(future.customer_id)
    g["days_since"] = (today - g.last_visit).dt.days
    g["expected_gap"] = g.median_gap.fillna(g.median_gap.median()).clip(14, 120)
    g["overdue_days"] = g.days_since - g.expected_gap
    g["overdue"] = (g.visits >= 2) & (~g.booked) & (g.overdue_days > 0.3 * g.expected_gap) & (g.days_since <= 365)
    c = d.customers.set_index("id")
    g["phone"] = g.index.map(c.phone) if "phone" in c else ""
    g["email"] = g.index.map(c.email) if "email" in c else ""
    return g.reset_index()


def cohort_retention(d: Data, keys=None, months=12, today=None) -> pd.DataFrame:
    """Cohort = month of first finished visit. Value = % of cohort with a finished visit
    in month k after their first (k = 1..months). Shape: index cohort, columns 1..months."""
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    a = _finished_sorted(d, keys)
    a = a[a.appointment_date <= today]
    first = a.groupby("customer_id").appointment_date.min().dt.to_period("M")
    a = a.assign(cohort=a.customer_id.map(first), period=a.appointment_date.dt.to_period("M"))
    a["k"] = (a.period - a.cohort).apply(lambda x: x.n)
    sizes = a[a.k == 0].groupby("cohort").customer_id.nunique()
    act = a[a.k.between(1, months)].groupby(["cohort", "k"]).customer_id.nunique().unstack(fill_value=0)
    ret = act.div(sizes, axis=0)
    ret.insert(0, "customers", sizes)
    cutoff = (today - pd.DateOffset(months=months + 1)).to_period("M")
    ret = ret[ret.index >= cutoff] if len(ret) else ret
    ret.index = ret.index.astype(str)
    return ret


def second_visit(d: Data, keys=None, today=None, within=90) -> pd.DataFrame:
    """New-customer conversion: of customers whose first finished visit fell in month X,
    % who had a 2nd finished visit within `within` days."""
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    a = _finished_sorted(d, keys)
    firsts = a.groupby("customer_id").appointment_date.nsmallest(2).reset_index(level=0)
    firsts["rank"] = firsts.groupby("customer_id").cumcount()
    piv = firsts.pivot(index="customer_id", columns="rank", values="appointment_date")
    piv = piv[piv[0] <= today - pd.Timedelta(days=within)]
    piv["converted"] = (piv.get(1) - piv[0]).dt.days.le(within)
    piv["month"] = piv[0].dt.to_period("M").dt.to_timestamp()
    out = piv.groupby("month").agg(new_customers=("converted", "size"), converted=("converted", "sum")).reset_index()
    out["rate"] = out.converted / out.new_customers
    return out.tail(18)


def cancellations(d: Data, start, end, keys=None) -> pd.DataFrame:
    a = for_locations(in_range(d.appointments, "appointment_date", start, end), keys)
    c = a[(a.status == "CANCELED") & a.canceled_utc.notna()].copy()
    c["hours_before"] = (c.start_utc - c.canceled_utc).dt.total_seconds() / 3600
    c["last_minute"] = c.hours_before < 24
    c["week"] = _grain(c.appointment_date, "week")
    return c


def cancel_summary(c: pd.DataFrame, by="location_name") -> pd.DataFrame:
    g = c.groupby(by).agg(canceled=("id", "count"), last_minute=("last_minute", "sum"), lost_value=("total_amount", "sum"),
                          median_hours_before=("hours_before", "median"))
    g["last_minute_share"] = g.last_minute / g.canceled
    return g.sort_values("lost_value", ascending=False).reset_index()


def lead_time(d: Data, start, end, keys=None) -> pd.DataFrame:
    a = for_locations(in_range(d.appointments, "appointment_date", start, end), keys)
    a = a[a.created_utc.notna()].copy()
    a["lead_days"] = (a.appointment_date - a.created_utc.dt.normalize()).dt.days.clip(lower=0)
    bins = [-1, 0, 2, 6, 13, 27, 55, 10_000]
    labels = ["Same day", "1-2 days", "3-6 days", "1-2 weeks", "2-4 weeks", "4-8 weeks", "8+ weeks"]
    a["lead_bucket"] = pd.cut(a.lead_days, bins, labels=labels)
    return a


def utilization(d: Data, start, end, keys=None, day_hours=8.0) -> pd.DataFrame:
    """Scheduled finished minutes per groomer per working day vs an assumed day length."""
    a = for_locations(in_range(d.appointments, "appointment_date", start, end), keys)
    a = a[(a.status == "FINISHED") & a.primary_staff_id.notna()].copy()
    a["staff_name"] = a.primary_staff_id.map(d.staff_names).fillna(a.primary_staff_id)
    daily = a.groupby(["staff_name", "location_name", "appointment_date"]).agg(
        minutes=("scheduled_minutes", "sum"), appts=("id", "count"), value=("total_amount", "sum")).reset_index()
    g = daily.groupby(["staff_name", "location_name"]).agg(
        working_days=("appointment_date", "nunique"), avg_minutes_per_day=("minutes", "mean"),
        avg_appts_per_day=("appts", "mean"), avg_value_per_day=("value", "mean"))
    g["utilization"] = g.avg_minutes_per_day / (day_hours * 60)
    return g[g.working_days >= 5].sort_values("utilization", ascending=False).reset_index()


def tips_and_discounts(d: Data, start, end, keys=None, by="location_name") -> pd.DataFrame:
    o = for_locations(in_range(d.orders, "sales_date", start, end), keys)
    o = o[o.status == "COMPLETED"]
    g = o.groupby(by).agg(orders=("id", "count"), sub_total=("sub_total", "sum"), tips=("tips_amount", "sum"),
                          discounts=("discount_amount", "sum"), tax=("tax_amount", "sum"))
    g["tip_rate"] = g.tips / g.sub_total.replace(0, np.nan)
    g["discount_rate"] = g.discounts / (g.sub_total + g.discounts).replace(0, np.nan)
    return g.sort_values("tip_rate", ascending=False).reset_index()


def ltv_by_source(d: Data, keys=None) -> pd.DataFrame:
    c = for_locations(d.customers, keys)[["id", "source", "referral_source", "created_date"]].copy()
    c["acq"] = c.referral_source.fillna("").replace("", np.nan).fillna(c.source).fillna("Unknown").replace("", "Unknown")
    a = d.appointments[d.appointments.status == "FINISHED"].groupby("customer_id").agg(
        visits=("id", "count"), value=("total_amount", "sum"))
    c = c.join(a, on="id")
    g = c.groupby("acq").agg(customers=("id", "count"), visited=("visits", lambda s: s.notna().sum()),
                             avg_visits=("visits", "mean"), avg_value=("value", "mean"), total_value=("value", "sum"))
    g["visit_conversion"] = g.visited / g.customers
    return g[g.customers >= 25].sort_values("total_value", ascending=False).reset_index()
