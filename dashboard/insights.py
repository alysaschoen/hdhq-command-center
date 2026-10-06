"""Insights engine: turns the metric layer into ranked, dollar-quantified findings.

Each finding is a dict:
    key, title, headline ($ or %), impact (annualised $), severity ('risk'|'opportunity'|'win'),
    body (one paragraph), action (one line), scope (location or 'Network'),
    evidence (DataFrame used by the drill page), chart (callable(fig)->None, optional)
Findings are recomputed from data every run — nothing here is hard-coded.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import metrics as m

log = logging.getLogger(__name__)


def _money(x):
    return f"${x:,.0f}"


def _pct(x):
    return f"{x * 100:.0f}%"


def compute(d: m.Data, asof: pd.Timestamp, keys=None) -> list[dict]:
    start, end = (asof - pd.DateOffset(months=12)).date(), asof.date()
    out: list[dict] = []
    locn = d.location_names

    # ---- Rebooking gap
    try:
        fin = m.rebooking(d, start, end, keys)
        if len(fin):
            rs = m.rebooking_summary(fin)
            rs["avg_ticket"] = fin.groupby("location_name").total_amount.mean().reindex(rs.location_name).values
            target = rs[rs.visits >= 100].rebook_rate.quantile(0.75)
            rs["gap_visits"] = (target - rs.rebook_rate).clip(lower=0) * rs.visits
            rs["opportunity"] = rs.gap_visits * rs.avg_ticket
            net_rate = fin.rebooked.mean()
            worst = rs.sort_values("opportunity", ascending=False).iloc[0]
            out.append(dict(
                key="rebooking", severity="opportunity", scope="Network",
                title="Rebooking at checkout is the biggest lever",
                headline=_money(rs.opportunity.sum()) + "/yr",
                impact=float(rs.opportunity.sum()),
                body=(f"{_pct(net_rate)} of finished visits get the next appointment booked within 7 days; the top quartile of "
                      f"locations does {_pct(target)}. {worst.location_name} alone ({_pct(worst.rebook_rate)} on {int(worst.visits):,} visits) "
                      f"is worth {_money(worst.opportunity)}/yr at the top-quartile rate."),
                action=f"Checkout rebooking script + MoeGo 'book next' prompt; start with {worst.location_name}.",
                evidence=rs.sort_values("opportunity", ascending=False), metric_col="rebook_rate", target=target))
    except Exception as exc:  # a finding that can't be computed for this slice is skipped, not fatal
        log.warning('finding %s skipped: %s', 'Rebooking gap', exc)

    # ---- Repeat cancelers
    try:
        cn = m.cancellations(d, start, end, keys)
        if len(cn):
            cc = cn.groupby("customer_id").agg(n=("id", "count"), lost=("total_amount", "sum"), location_name=("location_name", "last"),
                                             name=("customer_name", "last") if "customer_name" in cn else ("customer_id", "last"))
            rep = cc[cc.n >= 3]
            share = rep.n.sum() / len(cn)
            out.append(dict(
                key="cancels", severity="risk", scope="Network",
                title="Half of all cancellations come from repeat cancelers",
                headline=_money(cn.total_amount.sum()) + " lost",
                impact=float(rep.lost.sum()),
                body=(f"{len(cn):,} cancellations in 12 months ({_money(cn.total_amount.sum())} of booked value). "
                      f"{len(rep):,} customers with 3+ cancels caused {_pct(share)} of them ({_money(rep.lost.sum())}). "
                      f"{_pct(cn.last_minute.mean())} of cancels came with under 24 hours' notice."),
                action="Deposit or cancellation fee for customers with 3+ cancels; 48-hour confirmation text for everyone.",
                evidence=rep.sort_values("lost", ascending=False).reset_index(), summary=m.cancel_summary(cn)))
    except Exception as exc:  # a finding that can't be computed for this slice is skipped, not fatal
        log.warning('finding %s skipped: %s', 'Repeat cancelers', exc)

    # ---- New-customer leakage
    try:
        fs = m._finished_sorted(d, keys)
        if len(fs):
            first = fs.groupby("customer_id").first()
            cnt = fs.groupby("customer_id").size()
            first["one_time"] = cnt.reindex(first.index).eq(1)
            mature = first[first.appointment_date.between(asof - pd.DateOffset(months=15), asof - pd.Timedelta(days=120))]
            byloc = mature.groupby("location_name").one_time.agg(["mean", "size"]).rename(columns={"mean": "one_and_done", "size": "new_customers"})
            byloc = byloc[byloc.new_customers >= 30].sort_values("one_and_done", ascending=False)
            rate = mature.one_time.mean()
            lost_cust = int(mature.one_time.sum())
            avg_ltv = fs.groupby("customer_id").total_amount.sum()[cnt >= 2].mean()
            out.append(dict(
                key="leakage", severity="risk", scope="Network",
                title="Most new customers never come back",
                headline=_pct(rate) + " one-and-done",
                impact=float(lost_cust * 0.25 * avg_ltv),   # if a quarter of them were retained at avg LTV
                body=(f"Of {len(mature):,} customers whose first visit was 4–15 months ago, {_pct(rate)} never returned. "
                      f"Retained customers are worth {_money(avg_ltv)} on average; keeping one in four of the lost ones is "
                      f"{_money(lost_cust * 0.25 * avg_ltv)}. Worst: " +
                      ", ".join(f"{i} {_pct(r.one_and_done)}" for i, r in byloc.head(3).iterrows()) + "."),
                action="Post-first-visit follow-up (photo + rebook link within 48h) and a 2nd-visit offer; track 2nd-visit rate weekly.",
                evidence=byloc.reset_index(), metric_col="one_and_done"))
    except Exception as exc:  # a finding that can't be computed for this slice is skipped, not fatal
        log.warning('finding %s skipped: %s', 'New-customer leakage', exc)

    # ---- Overdue customers
    try:
        cad = m.cadence(d, keys, today=asof)
        od = cad[cad.overdue]
        if len(od):
            byl = od.groupby("location_name").agg(n=("customer_id", "size"), value=("avg_ticket", "sum")).sort_values("value", ascending=False)
            out.append(dict(
                key="overdue", severity="opportunity", scope="Network",
                title="Overdue customers with nothing booked",
                headline=f"{len(od):,} customers · {_money(od.avg_ticket.sum())}",
                impact=float(od.avg_ticket.sum()),
                body=(f"{len(od):,} customers with 2+ visits are past their own usual interval (30%+ over their median gap) and have no "
                      f"future booking. One visit each at their usual ticket is {_money(od.avg_ticket.sum())}. "
                      f"Most $ waiting: " + ", ".join(f"{i} {int(r.n)} ({_money(r.value)})" for i, r in byl.head(3).iterrows()) + "."),
                action="Export the list; text/call in order of avg ticket. Repeat weekly — the list regenerates after every sync.",
                evidence=od.sort_values("avg_ticket", ascending=False)))
    except Exception as exc:  # a finding that can't be computed for this slice is skipped, not fatal
        log.warning('finding %s skipped: %s', 'Overdue customers', exc)

    # ---- Lead source conversion
    try:
        lt = m.ltv_by_source(d, keys)
        if len(lt) >= 2:
            big = lt[lt.customers >= 500]
            worst = big.sort_values("visit_conversion").iloc[0]
            best = big.sort_values("visit_conversion").iloc[-1]
            extra = (best.visit_conversion - worst.visit_conversion) * worst.customers * 0.5 * (worst.avg_value if pd.notna(worst.avg_value) else 0)
            out.append(dict(
                key="sources", severity="opportunity", scope="Network",
                title="Lead handoff, not lead volume, is the acquisition problem",
                headline=f"{worst.acq}: {_pct(worst.visit_conversion)} convert",
                impact=float(extra),
                body=(f"'{worst.acq}' is a top source ({int(worst.customers):,} leads) but only {_pct(worst.visit_conversion)} ever take a first visit, "
                      f"vs {_pct(best.visit_conversion)} for '{best.acq}'. The '{worst.acq}' leads that do convert are worth {_money(worst.avg_value)} "
                      f"each — the audience is fine, the follow-up isn't."),
                action=f"Speed-to-lead SLA for '{worst.acq}' leads: same-day reply with a booking link in the first message.",
                evidence=lt))
    except Exception as exc:  # a finding that can't be computed for this slice is skipped, not fatal
        log.warning('finding %s skipped: %s', 'Lead source conversion', exc)

    # ---- Utilization
    try:
        ut = m.utilization(d, start, end, keys)
        if len(ut):
            low = ut[ut.utilization < 0.6]
            out.append(dict(
                key="capacity", severity="risk" if len(low) / len(ut) > 0.4 else "win", scope="Network",
                title="Groomer capacity is not the constraint",
                headline=f"{len(low)} of {len(ut)} groomers under 60%",
                impact=float(((0.75 - low.utilization).clip(lower=0) * low.working_days * low.avg_value_per_day / low.utilization.replace(0, np.nan)).sum()),
                body=(f"Median scheduled time is {_pct(ut.utilization.median())} of an 8-hour day. {len(low)} groomers average under 60% "
                      f"({', '.join(low.head(3).staff_name)}…). Filling them to 75% is worth roughly "
                      f"{_money(((0.75 - low.utilization).clip(lower=0) * low.working_days * low.avg_value_per_day / low.utilization.replace(0, np.nan)).sum())}/yr "
                      f"in service value — demand and scheduling, not hiring, is the lever."),
                action="Route overdue/win-back outreach to the under-utilized groomers' territories first; review route density.",
                evidence=ut))
    except Exception as exc:  # a finding that can't be computed for this slice is skipped, not fatal
        log.warning('finding %s skipped: %s', 'Utilization', exc)

    # ---- YoY momentum
    try:
        P = d.payments if keys is None else d.payments[d.payments.location_key.isin(keys)]
        P = P.assign(mo=P.payment_date.dt.to_period("M"))
        cur_m = pd.period_range(asof - pd.DateOffset(months=3), asof, freq="M")[1:]
        ly_m = pd.period_range(asof - pd.DateOffset(months=15), asof - pd.DateOffset(months=12), freq="M")[1:]
        cur = P[P.mo.isin(cur_m)].groupby("location_name").net.sum()
        ly = P[P.mo.isin(ly_m)].groupby("location_name").net.sum()
        yoy = pd.DataFrame({"last3": cur, "ly3": ly}).fillna(0)
        yoy = yoy[yoy.ly3 >= 5000]
        yoy["chg"] = (yoy.last3 - yoy.ly3) / yoy.ly3
        yoy["delta"] = yoy.last3 - yoy.ly3
        decl = yoy[yoy.chg < -0.15].sort_values("delta")
        if len(decl):
            out.append(dict(
                key="decline", severity="risk", scope=", ".join(decl.index[:3]),
                title="Locations shrinking year over year",
                headline=f"{len(decl)} locations down 15%+",
                impact=float(-decl.delta.sum() * 4),
                body=("Last 3 months vs the same months last year: " +
                      ", ".join(f"{i} {_pct(r.chg)} ({_money(r.delta)})" for i, r in decl.head(4).iterrows()) +
                      f". Annualised, that's {_money(-decl.delta.sum() * 4)} of revenue run-rate lost."),
                action="Pull each declining location's rebook %, cancel %, and groomer count — they tell different stories (see drill-down).",
                evidence=yoy.sort_values("chg").reset_index()))
    except Exception as exc:  # a finding that can't be computed for this slice is skipped, not fatal
        log.warning('finding %s skipped: %s', 'YoY momentum', exc)

    # ---- Tips config
    try:
        td = m.tips_and_discounts(d, start, end, keys)
        zero = td[(td.orders >= 100) & (td.tip_rate < 0.03)]
        if len(zero):
            med = td.tip_rate.median()
            out.append(dict(
                key="tips", severity="risk", scope=", ".join(zero.location_name),
                title="Tips aren't being captured at some locations",
                headline=", ".join(f"{r.location_name} {_pct(r.tip_rate)}" for _, r in zero.iterrows()),
                impact=float(((med - zero.tip_rate) * zero.sub_total).sum()),
                body=(f"Network median tip rate is {_pct(med)} of subtotal; " +
                      ", ".join(f"{r.location_name} shows {_pct(r.tip_rate)} on {int(r.orders)} orders" for _, r in zero.iterrows()) +
                      f". That's {_money(((med - zero.tip_rate) * zero.sub_total).sum())}/yr of groomer income — almost certainly a "
                      f"payment-flow setting, not customer behaviour."),
                action="Check tip prompt / card-reader settings in MoeGo for those locations.",
                evidence=td))
    except Exception as exc:  # a finding that can't be computed for this slice is skipped, not fatal
        log.warning('finding %s skipped: %s', 'Tips config', exc)

    out.sort(key=lambda f: -f["impact"])
    return out
