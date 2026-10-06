"""HDHQ Operations — leadership dashboard (Streamlit + Plotly).

    streamlit run dashboard/app.py

Reads the local warehouse (data/hdhq.sqlite) maintained by sync.py — the same
rows that land in the HDHQ Operations workspace in Zoho Analytics.
All charts are plain Plotly: edit freely. Number-crunching lives in metrics.py.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import metrics as m

st.set_page_config(page_title="HDHQ Operations", page_icon="🐾", layout="wide")

# --- palette (fixed categorical order; one hue for magnitude) ------------------------------
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SEQ = "Blues"
GOOD, BAD, MUTED = "#008300", "#e34948", "#9a9a95"
BUCKET_COLORS = {"Booked": "#2a78d6", "Active (0-60d)": "#1baf7a", "Cooling (61-120d)": "#eda100",
                 "Lapsed (120d+)": "#e34948", "Never visited": "#9a9a95"}
LAYOUT = dict(template="plotly_white", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
              margin=dict(l=10, r=10, t=44, b=10), font=dict(size=13), hoverlabel=dict(font_size=13),
              xaxis=dict(showgrid=False), yaxis=dict(gridcolor="rgba(128,128,128,0.18)", zeroline=False),
              legend=dict(orientation="h", y=-0.2), bargap=0.25)


def fig_style(fig, title=None, **kw):
    fig.update_layout(**{**LAYOUT, **kw})   # per-chart kwargs override the defaults
    if title:
        fig.update_layout(title=dict(text=title, x=0, font=dict(size=16)))
    fig.update_traces(marker_line_width=0, selector=dict(type="bar"))
    return fig


money = lambda x: f"${x:,.0f}"
pct = lambda x: f"{(x or 0) * 100:.1f}%"


def delta_pct(cur, prev, suffix=" vs prior"):
    return None if not prev else f"{(cur - prev) / prev * 100:+.1f}%{suffix}"


def kpi_row(items):
    cols = st.columns(len(items))
    for col, it in zip(cols, items):
        label, value, delta, invert = (list(it) + [None, False])[:4]
        col.metric(label, value, delta, delta_color="inverse" if invert else "normal")


def hbar(df, x, y, title, color=None, text=None, prefix="", fmt=None, **kw):
    df = df.sort_values(x)
    fig = px.bar(df, x=x, y=y, orientation="h", color=color if color else None,
                 color_discrete_sequence=[CAT[0]] if not color else None,
                 color_continuous_scale=SEQ if color else None, text=text)
    fig.update_traces(textposition="outside", cliponaxis=False)
    return fig_style(fig, title, coloraxis_showscale=False, xaxis_tickprefix=prefix,
                     xaxis_tickformat=fmt, yaxis_title=None, xaxis_title=None, **kw)


# --- data -----------------------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner="Loading warehouse…")
def get_data():
    return m.load()


d = get_data()
loc_df = d.locations.sort_values("location_name")
# "Today" = latest day with collected payments, so tiles stay meaningful between syncs.
asof = pd.Timestamp(d.payments.payment_date.max()).normalize()
today = asof.date()

# --- sidebar --------------------------------------------------------------------------------
PAGES = ["Pulse", "Map", "Revenue & Volume", "Retention & Rebooking", "Cancellations & Capacity", "Location Scorecard"]
with st.sidebar:
    st.title("🐾 HDHQ Operations")
    page = st.radio("Dashboard", PAGES, label_visibility="collapsed")
    st.divider()
    preset = st.selectbox("Period", ["Last 7 days", "Last 14 days", "Last 30 days", "Last 90 days", "This month",
                                     "Last month", "Year to date", "Last 12 months", "Custom"], index=3)
    if preset.startswith("Last ") and preset.endswith("days"):
        start, end = today - timedelta(days=int(preset.split()[1]) - 1), today
    elif preset == "This month":
        start, end = today.replace(day=1), today
    elif preset == "Last month":
        first = today.replace(day=1)
        start, end = (first - timedelta(days=1)).replace(day=1), first - timedelta(days=1)
    elif preset == "Year to date":
        start, end = today.replace(month=1, day=1), today
    elif preset == "Last 12 months":
        start, end = (today - pd.DateOffset(months=12)).date(), today
    else:
        start, end = st.date_input("Range", (today - timedelta(days=90), today))
    regions = sorted(loc_df.region.dropna().unique())
    sel_regions = st.multiselect("Region", regions, default=[])
    opts = loc_df[loc_df.region.isin(sel_regions)] if sel_regions else loc_df
    sel_locs = st.multiselect("Locations", opts.location_name.tolist(), default=[], placeholder="All locations")
    keys = opts[opts.location_name.isin(sel_locs)].location_key.tolist() if sel_locs else opts.location_key.tolist()
    if not sel_regions and not sel_locs:
        keys = None
    st.caption(f"{start:%b %d, %Y} – {end:%b %d, %Y}")
    st.caption(f"Data as of {asof:%b %d, %Y}")

pstart, pend = m.prior_period(start, end)
scope = "All locations" if keys is None else (", ".join(sorted(sel_locs)) if sel_locs else ", ".join(sel_regions))

# ===============================================================================================
if page == "Pulse":
    st.header("Pulse")
    st.caption(f"{scope} · what happened this week vs last, every day")
    grain = st.radio("Grain", ["day", "week"], horizontal=True, label_visibility="collapsed")
    k7, k7p = m.kpis(d, today - timedelta(days=6), today, keys), m.kpis(d, today - timedelta(days=13), today - timedelta(days=7), keys)
    kpi_row([
        ("Revenue, last 7 days", money(k7["net_revenue"]), delta_pct(k7["net_revenue"], k7p["net_revenue"], " vs prior 7d")),
        ("Finished, last 7 days", f"{k7['finished']:,}", delta_pct(k7["finished"], k7p["finished"], " vs prior 7d")),
        ("Cancel rate, 7d", pct(k7["cancel_rate"]), delta_pct(k7["cancel_rate"], k7p["cancel_rate"], ""), True),
        ("New customers, 7d", f"{k7['new_customers']:,}", delta_pct(k7["new_customers"], k7p["new_customers"], "")),
        ("Avg ticket, 7d", money(k7["avg_ticket"]), delta_pct(k7["avg_ticket"], k7p["avg_ticket"], "")),
    ])
    span = 42 if grain == "day" else 112
    p = m.pulse(d, today - timedelta(days=span), today, keys, grain)
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        fig.add_bar(x=p.bucket, y=p.net_revenue, name="Net revenue", marker_color=CAT[0], opacity=0.55,
                    hovertemplate="%{x|%a %b %d}<br>$%{y:,.0f}<extra></extra>")
        fig.add_scatter(x=p.bucket, y=p.revenue_ma, name=f"{7 if grain == 'day' else 4}-{grain} avg", mode="lines",
                        line=dict(color=CAT[0], width=2.5), hovertemplate="avg $%{y:,.0f}<extra></extra>")
        st.plotly_chart(fig_style(fig, f"Net revenue per {grain}", yaxis_tickprefix="$"), use_container_width=True)
    with c2:
        fig = go.Figure()
        fig.add_bar(x=p.bucket, y=p.finished, name="Finished", marker_color=CAT[0])
        fig.add_bar(x=p.bucket, y=p.canceled, name="Canceled", marker_color=CAT[1])
        fig.add_bar(x=p.bucket, y=p.no_shows, name="No-show", marker_color=CAT[7])
        st.plotly_chart(fig_style(fig, f"Appointments per {grain} by outcome", barmode="stack"), use_container_width=True)
    c3, c4 = st.columns(2)
    with c3:
        fig = px.bar(p, x="bucket", y="lost_value", color_discrete_sequence=[CAT[7]])
        fig.update_traces(hovertemplate="%{x|%b %d}<br>$%{y:,.0f} lost<extra></extra>")
        st.plotly_chart(fig_style(fig, f"Revenue lost to cancels & no-shows per {grain}", yaxis_tickprefix="$",
                                  xaxis_title=None, yaxis_title=None), use_container_width=True)
    with c4:
        mo = m.momentum(d, keys, weeks=4, today=asof).dropna(subset=["revenue_chg"])
        fig = px.bar(mo.sort_values("revenue_chg"), x="revenue_chg", y="location_name", orientation="h",
                     color=mo.sort_values("revenue_chg").revenue_chg.ge(0).map({True: "up", False: "down"}),
                     color_discrete_map={"up": GOOD, "down": BAD},
                     hover_data={"revenue_cur": ":$,.0f", "revenue_prev": ":$,.0f", "finished_chg": ":+.1%"})
        st.plotly_chart(fig_style(fig, "Momentum: last 4 weeks vs prior 4 weeks (revenue)", xaxis_tickformat="+.0%",
                                  showlegend=False, yaxis_title=None, xaxis_title=None), use_container_width=True)
    with st.expander("By location, weekly (finished appointments)"):
        pl = m.pulse_by_location(d, today - timedelta(days=84), today, keys, "week")
        piv = pl.pivot(index="location_name", columns="bucket", values="finished").fillna(0)
        piv.columns = [c.strftime("%b %d") for c in piv.columns]
        fig = px.imshow(piv, color_continuous_scale=SEQ, aspect="auto", text_auto=".0f",
                        labels=dict(x="Week of", y="", color="Finished"))
        fig.update_traces(hovertemplate="%{y}<br>week of %{x}: %{z} finished<extra></extra>")
        st.plotly_chart(fig_style(fig, None, coloraxis_showscale=False, height=max(300, 28 * len(piv) + 80)),
                        use_container_width=True)

# ===============================================================================================
elif page == "Map":
    st.header("Map")
    metric = st.selectbox("Color & size by", ["Net revenue", "Finished appointments", "Lost to cancellations ($)",
                                              "Lapsed customers", "Leads never converted", "Avg ticket", "Cancel rate"])
    layer = st.toggle("Customer density heat layer", value=False)
    sc = m.scorecard(d, start, end, None).set_index("location_key")
    act = m.activity_buckets(d, today=asof)
    lapsed = act[act.activity_bucket == "Lapsed (120d+)"].groupby("location_key").size()
    never = act[act.activity_bucket == "Never visited"].groupby("location_key").size()
    canc = m.cancel_summary(m.cancellations(d, start, end), by="location_key").set_index("location_key")
    g = loc_df.set_index("location_key").join(sc[["net_revenue", "finished", "avg_ticket", "cancel_rate", "appointments"]])
    g["lost"] = canc.lost_value
    g["lapsed"] = lapsed
    g["never"] = never
    g = g.fillna(0).reset_index()
    col = {"Net revenue": "net_revenue", "Finished appointments": "finished", "Lost to cancellations ($)": "lost",
           "Lapsed customers": "lapsed", "Leads never converted": "never", "Avg ticket": "avg_ticket",
           "Cancel rate": "cancel_rate"}[metric]
    g = g[g.lat.notna()]
    fig = go.Figure()
    if layer:
        cg = d.customers[d.customers.lat.notna() & d.customers.lng.notna()]
        if keys:
            cg = cg[cg.location_key.isin(keys)]
        fig.add_densitymap(lat=cg.lat, lon=cg.lng, radius=12, colorscale="Blues", showscale=False, opacity=0.6,
                              hoverinfo="skip", name="Customers")
    size = g[col].clip(lower=0)
    size = 12 + 38 * (size / size.max()) if size.max() else 20
    fig.add_scattermap(
        lat=g.lat, lon=g.lng, mode="markers+text", text=g.location_name, textposition="top center",
        marker=dict(size=size, color=g[col], colorscale="Blues" if col != "cancel_rate" else "Reds",
                    showscale=True, colorbar=dict(title=metric, thickness=12), opacity=0.9),
        customdata=np.stack([g.net_revenue, g.finished, g.lost, g.lapsed, g.never, g.avg_ticket, g.cancel_rate], axis=1),
        hovertemplate="<b>%{text}</b><br>Net revenue $%{customdata[0]:,.0f}<br>Finished %{customdata[1]:,.0f}"
                      "<br>Lost to cancels $%{customdata[2]:,.0f}<br>Lapsed customers %{customdata[3]:,.0f}"
                      "<br>Leads never converted %{customdata[4]:,.0f}<br>Avg ticket $%{customdata[5]:,.0f}"
                      "<br>Cancel rate %{customdata[6]:.1%}<extra></extra>")
    fig.update_layout(map=dict(style="carto-positron", center=dict(lat=38.5, lon=-93), zoom=3.4), height=620,
                      margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Bubble size and color = selected metric for the selected period (activity buckets are as of today). "
               "Toggle the heat layer to see where customers actually live relative to each van territory.")
    st.dataframe(g[["location_name", "state", "net_revenue", "finished", "avg_ticket", "cancel_rate", "lost", "lapsed", "never"]]
                 .sort_values(col, ascending=False), hide_index=True, use_container_width=True,
                 column_config={"net_revenue": st.column_config.NumberColumn("Net revenue", format="$%,.0f"),
                                "finished": "Finished", "avg_ticket": st.column_config.NumberColumn("Avg ticket", format="$%.0f"),
                                "cancel_rate": st.column_config.NumberColumn("Cancel rate", format="%.1%%"),
                                "lost": st.column_config.NumberColumn("Lost to cancels", format="$%,.0f"),
                                "lapsed": "Lapsed", "never": "Leads never converted"})

# ===============================================================================================
elif page == "Revenue & Volume":
    k, kp = m.kpis(d, start, end, keys), m.kpis(d, pstart, pend, keys)
    st.header("Revenue & Volume")
    st.caption(f"{scope} · revenue = payments collected minus refunds")
    kpi_row([
        ("Net revenue", money(k["net_revenue"]), delta_pct(k["net_revenue"], kp["net_revenue"])),
        ("Finished appointments", f"{k['finished']:,}", delta_pct(k["finished"], kp["finished"])),
        ("Avg ticket", money(k["avg_ticket"]), delta_pct(k["avg_ticket"], kp["avg_ticket"])),
        ("Cancel rate", pct(k["cancel_rate"]), delta_pct(k["cancel_rate"], kp["cancel_rate"]), True),
        ("No-show rate", pct(k["no_show_rate"]), delta_pct(k["no_show_rate"], kp["no_show_rate"]), True),
        ("New customers", f"{k['new_customers']:,}", delta_pct(k["new_customers"], kp["new_customers"])),
    ])
    trend_start = min(pd.Timestamp(start), pd.Timestamp(end) - pd.DateOffset(months=24))
    c1, c2 = st.columns([3, 2])
    with c1:
        rev = m.monthly_revenue(d, trend_start, end, keys)
        fig = go.Figure(go.Scatter(x=rev.month, y=rev.net_revenue, mode="lines+markers", line=dict(color=CAT[0], width=2.5),
                                   marker=dict(size=7), hovertemplate="%{x|%b %Y}<br>$%{y:,.0f}<extra></extra>"))
        fig.add_vrect(x0=pd.Timestamp(start), x1=pd.Timestamp(end), fillcolor=CAT[0], opacity=0.07, line_width=0)
        st.plotly_chart(fig_style(fig, "Monthly net revenue (selected period shaded)", yaxis_tickprefix="$", showlegend=False),
                        use_container_width=True)
    with c2:
        rl = m.revenue_by_location(d, start, end, keys)
        st.plotly_chart(hbar(rl, "net_revenue", "location_name", "Net revenue by location", color="net_revenue",
                             text=rl.sort_values("net_revenue").net_revenue.map(money), prefix="$"), use_container_width=True)
    c3, c4 = st.columns([3, 2])
    with c3:
        ap = m.monthly_appointments(d, trend_start, end, keys)
        fig = go.Figure()
        fig.add_bar(x=ap.month, y=ap.finished, name="Finished", marker_color=CAT[0])
        fig.add_bar(x=ap.month, y=ap.canceled, name="Canceled", marker_color=CAT[1])
        fig.add_bar(x=ap.month, y=ap.no_shows, name="No-show", marker_color=CAT[7])
        st.plotly_chart(fig_style(fig, "Monthly appointments by outcome", barmode="stack"), use_container_width=True)
    with c4:
        sm = m.service_mix(d, start, end, keys)
        st.plotly_chart(hbar(sm, "value", "service_name", "Top services by finished value",
                             text=sm.sort_values("value").value.map(money), prefix="$"), use_container_width=True)
    td = m.tips_and_discounts(d, start, end, keys)
    c5, c6 = st.columns(2)
    with c5:
        st.plotly_chart(hbar(td, "tip_rate", "location_name", "Tip rate (tips ÷ subtotal)", fmt=".0%",
                             text=td.sort_values("tip_rate").tip_rate.map(pct)), use_container_width=True)
    with c6:
        lt = m.ltv_by_source(d, keys)
        fig = px.scatter(lt, x="visit_conversion", y="avg_value", size="customers", text="acq", size_max=50,
                         color_discrete_sequence=[CAT[6]])
        fig.update_traces(textposition="top center",
                          hovertemplate="%{text}<br>%{marker.size:,} customers<br>convert to a visit %{x:.0%}<br>avg lifetime value $%{y:,.0f}<extra></extra>")
        st.plotly_chart(fig_style(fig, "Acquisition source: conversion vs lifetime value (bubble = customers)",
                                  xaxis_tickformat=".0%", yaxis_tickprefix="$", xaxis_title="Convert to a first visit",
                                  yaxis_title="Avg lifetime value (visited)"), use_container_width=True)

# ===============================================================================================
elif page == "Retention & Rebooking":
    st.header("Retention & Rebooking")
    st.caption(f"{scope} · the levers MoeGo doesn't report")
    fin = m.rebooking(d, start, end, keys)
    rs = m.rebooking_summary(fin)
    sv = m.second_visit(d, keys, today=asof)
    cad = m.cadence(d, keys, today=asof)
    od = cad[cad.overdue]
    kpi_row([
        ("Rebooked within 7 days", pct(fin.rebooked.mean() if len(fin) else 0), None),
        ("Any next visit booked", pct(fin.has_next.mean() if len(fin) else 0), None),
        ("2nd-visit conversion (90d)", pct(sv.tail(6).converted.sum() / max(sv.tail(6).new_customers.sum(), 1)), None),
        ("Overdue customers", f"{len(od):,}", None),
        ("Overdue revenue at stake", money((od.avg_ticket).sum()), None),
    ])
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(hbar(rs, "rebook_rate", "location_name", "Rebooking rate at checkout (next appt created ≤7 days after visit)",
                             fmt=".0%", text=rs.sort_values("rebook_rate").rebook_rate.map(pct), color="rebook_rate"), use_container_width=True)
    with c2:
        fig = go.Figure(go.Scatter(x=sv.month, y=sv.rate, mode="lines+markers", line=dict(color=CAT[0], width=2.5),
                                   marker=dict(size=8), customdata=sv.new_customers,
                                   hovertemplate="%{x|%b %Y}<br>%{y:.0%} of %{customdata} new customers came back within 90d<extra></extra>"))
        st.plotly_chart(fig_style(fig, "Second-visit conversion: % of new customers back within 90 days, by first-visit month",
                                  yaxis_tickformat=".0%", yaxis_range=[0, 1], showlegend=False), use_container_width=True)
    c3, c4 = st.columns([3, 2])
    with c3:
        co = m.cohort_retention(d, keys, months=9, today=asof)
        z = co.drop(columns="customers")
        fig = px.imshow(z, color_continuous_scale=SEQ, aspect="auto", text_auto=".0%", zmin=0, zmax=max(0.3, float(np.nanmax(z.values)) if z.size else 0.3),
                        labels=dict(x="Months after first visit", y="First-visit month", color="Returned"))
        fig.update_traces(hovertemplate="Cohort %{y}<br>month %{x}: %{z:.0%} returned<extra></extra>")
        st.plotly_chart(fig_style(fig, "Cohort retention: % of each first-visit cohort seen again in month N", coloraxis_showscale=False),
                        use_container_width=True)
    with c4:
        fig = px.histogram(cad[cad.visits >= 3], x="median_gap", nbins=30, color_discrete_sequence=[CAT[2]])
        fig.update_traces(hovertemplate="%{x} days<br>%{y} customers<extra></extra>")
        st.plotly_chart(fig_style(fig, "Visit cadence (median days between visits, 3+ visits)", xaxis_title="days",
                                  yaxis_title=None, bargap=0.05), use_container_width=True)
    st.subheader("Overdue for a groom — win-back list")
    st.caption("Customers with 2+ visits, no future booking, and now 30%+ past their own usual interval. Sorted by what they typically spend.")
    cols = ["customer_name", "location_name", "phone", "email", "visits", "last_visit", "median_gap", "days_since", "avg_ticket", "lifetime_value"]
    tbl = od.sort_values("avg_ticket", ascending=False)[cols]
    st.download_button("Download win-back list (CSV)", tbl.to_csv(index=False).encode(), "winback_list.csv", "text/csv")
    st.dataframe(tbl, hide_index=True, use_container_width=True, height=420,
                 column_config={"customer_name": "Customer", "location_name": "Location", "visits": "Visits",
                                "last_visit": st.column_config.DateColumn("Last visit", format="MMM D, YYYY"),
                                "median_gap": st.column_config.NumberColumn("Usual gap (days)", format="%.0f"),
                                "days_since": "Days since", "avg_ticket": st.column_config.NumberColumn("Avg ticket", format="$%.0f"),
                                "lifetime_value": st.column_config.NumberColumn("Lifetime value", format="$%,.0f")})

# ===============================================================================================
elif page == "Cancellations & Capacity":
    st.header("Cancellations & Capacity")
    st.caption(scope)
    cn = m.cancellations(d, start, end, keys)
    cs = m.cancel_summary(cn)
    lt = m.lead_time(d, start, end, keys)
    ut = m.utilization(d, start, end, keys)
    kpi_row([
        ("Canceled appointments", f"{len(cn):,}", None),
        ("Lost value (canceled)", money(cn.total_amount.sum()), None),
        ("Last-minute (<24h) share", pct(cn.last_minute.mean() if len(cn) else 0), None),
        ("Median notice (hours)", f"{cn.hours_before.median():.0f}" if len(cn) else "–", None),
        ("Median booking lead time", f"{lt.lead_days.median():.0f} days" if len(lt) else "–", None),
    ])
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(hbar(cs, "lost_value", "location_name", "Revenue lost to cancellations", color="lost_value",
                             text=cs.sort_values("lost_value").lost_value.map(money), prefix="$"), use_container_width=True)
    with c2:
        st.plotly_chart(hbar(cs, "last_minute_share", "location_name", "Share of cancels with <24h notice", fmt=".0%",
                             text=cs.sort_values("last_minute_share").last_minute_share.map(pct)), use_container_width=True)
    c3, c4 = st.columns(2)
    with c3:
        lb = lt.groupby("lead_bucket", observed=False).size().reset_index(name="appointments")
        fig = px.bar(lb, x="lead_bucket", y="appointments", color_discrete_sequence=[CAT[0]], text="appointments")
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig_style(fig, "Booking lead time (days between booking and appointment)", xaxis_title=None, yaxis_title=None),
                        use_container_width=True)
    with c4:
        wk = cn.groupby("week").agg(lost=("total_amount", "sum"), n=("id", "count")).reset_index()
        fig = px.bar(wk, x="week", y="lost", color_discrete_sequence=[CAT[7]], hover_data={"n": True})
        st.plotly_chart(fig_style(fig, "Lost value per week", yaxis_tickprefix="$", xaxis_title=None, yaxis_title=None), use_container_width=True)
    st.subheader("Groomer capacity")
    c5, c6 = st.columns(2)
    with c5:
        top = ut.head(25)
        st.plotly_chart(hbar(top, "utilization", "staff_name", "Utilization: scheduled minutes per working day ÷ 8h", fmt=".0%",
                             color="utilization", text=top.sort_values("utilization").utilization.map(pct)), use_container_width=True)
    with c6:
        lbd = m.groomer_leaderboard(d, start, end, keys).head(25)
        st.plotly_chart(hbar(lbd, "value_per_hour", "staff_name", "Service value per scheduled hour", prefix="$",
                             text=lbd.sort_values("value_per_hour").value_per_hour.map(money)), use_container_width=True)
    heat = m.weekday_hour_heat(d, start, end, keys)
    fig = px.imshow(heat, color_continuous_scale=SEQ, aspect="auto", labels=dict(x="Hour (local)", y="", color="Appointments"))
    fig.update_traces(hovertemplate="%{y} %{x}:00<br>%{z} appointments<extra></extra>")
    st.plotly_chart(fig_style(fig, "When finished appointments start (local time)"), use_container_width=True)

# ===============================================================================================
elif page == "Location Scorecard":
    st.header("Location Scorecard")
    st.caption(f"Selected period vs the prior period of equal length ({pstart:%b %d} – {pend:%b %d, %Y})")
    sc = m.scorecard(d, start, end, keys)
    rb = m.rebooking_summary(m.rebooking(d, start, end, keys)).set_index("location_name").rebook_rate
    sc["rebook_rate"] = sc.location_name.map(rb)
    view = sc[["location_name", "net_revenue", "net_revenue_chg", "finished", "finished_chg", "avg_ticket", "rebook_rate",
               "cancel_rate", "no_show_rate", "new_customers", "new_customers_chg", "customers", "groomers"]]
    st.dataframe(view, use_container_width=True, hide_index=True, height=640, column_config={
        "location_name": "Location",
        "net_revenue": st.column_config.NumberColumn("Net revenue", format="$%,.0f"),
        "net_revenue_chg": st.column_config.NumberColumn("Δ rev", format="%+.0%"),
        "finished": st.column_config.NumberColumn("Finished", format="%d"),
        "finished_chg": st.column_config.NumberColumn("Δ appts", format="%+.0%"),
        "avg_ticket": st.column_config.NumberColumn("Avg ticket", format="$%.0f"),
        "rebook_rate": st.column_config.ProgressColumn("Rebook %", format="%.0f%%", min_value=0, max_value=1),
        "cancel_rate": st.column_config.ProgressColumn("Cancel %", format="%.1f%%", min_value=0, max_value=0.5),
        "no_show_rate": st.column_config.ProgressColumn("No-show %", format="%.1f%%", min_value=0, max_value=0.2),
        "new_customers": st.column_config.NumberColumn("New customers", format="%d"),
        "new_customers_chg": st.column_config.NumberColumn("Δ new", format="%+.0%"),
        "customers": st.column_config.NumberColumn("Customers served", format="%d"),
        "groomers": st.column_config.NumberColumn("Active groomers", format="%d"),
    })
    c1, c2 = st.columns(2)
    with c1:
        chg = sc.dropna(subset=["net_revenue_chg"]).sort_values("net_revenue_chg")
        fig = px.bar(chg, x="net_revenue_chg", y="location_name", orientation="h",
                     color=chg.net_revenue_chg.ge(0).map({True: "up", False: "down"}), color_discrete_map={"up": GOOD, "down": BAD})
        st.plotly_chart(fig_style(fig, "Revenue change vs prior period", xaxis_tickformat="+.0%", showlegend=False,
                                  yaxis_title=None, xaxis_title=None), use_container_width=True)
    with c2:
        fig = px.scatter(sc, x="cancel_rate", y="rebook_rate", size="appointments", text="location_name", size_max=40,
                         color_discrete_sequence=[CAT[0]])
        fig.update_traces(textposition="top center")
        st.plotly_chart(fig_style(fig, "Cancel rate vs rebooking rate (bubble = appointments)", xaxis_tickformat=".0%",
                                  yaxis_tickformat=".0%", xaxis_title="Cancel rate", yaxis_title="Rebook rate"), use_container_width=True)
