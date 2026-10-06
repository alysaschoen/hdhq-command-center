"""HDHQ Command Center — one screen, sixty seconds, every number tied to a dollar.

Top:    KPI strip (trailing 7 days vs prior 7, 12-week sparklines)
Middle: ranked findings (computed by insights.py, sorted by $ impact)  |  revenue pulse
Bottom: location league table with inline bars                           |  metric map
Click a finding to open its drill-down: claim -> proof -> action list (CSV export).
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import insights
import metrics as m

st.set_page_config(page_title="HDHQ Command Center", page_icon="🐾", layout="wide",
                   initial_sidebar_state="collapsed")

# ------------------------------------------------------------------ theme (Hydrodog brand: franchising.hydrodog.com)
import base64
from pathlib import Path

BG, PANEL, PANEL2, LINE = "#F4F2EC", "#FFFFFF", "#ECE9DF", "#D6D2C4"      # cream / white / tan
INK, INK2, INK3 = "#2D2A29", "#5C5A54", "#8A877E"                         # charcoal
GREEN, SAGE, NAVY, SKY, LIME = "#3C4E39", "#8CA563", "#253D4E", "#BFD3DB", "#DDE797"
BRICK, SALMON, CREAM = "#B8493D", "#F2A094", "#F4F2EC"
BLUE, RED, AMBER, PINK, ORANGE = NAVY, BRICK, SAGE, SALMON, BRICK          # aliases used by charts
SEV = {"risk": ("RISK", BRICK), "opportunity": ("OPPORTUNITY", NAVY), "win": ("WORKING", SAGE)}
SERIF, SANS = '"new-spirit", Georgia, serif', '"dm-sans", "Helvetica Neue", Arial, sans-serif'

_logo = Path(__file__).with_name("assets") / "hydrodog-wordmark.png"
LOGO = base64.b64encode(_logo.read_bytes()).decode() if _logo.exists() else ""

st.markdown(f"""
<link rel="stylesheet" href="https://use.typekit.net/gfi2gkl.css">
<style>
  html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {{ background:{BG}; color:{INK}; font-family:{SANS}; }}
  [data-testid="stHeader"] {{ display:none; }}
  .block-container {{ padding:1.1rem 1.6rem 2rem 1.6rem; max-width:1800px; }}
  [data-testid="stSidebar"] {{ background:{PANEL}; }}
  h1,h2,h3,h4,p,li,span,div,label {{ color:{INK}; font-family:{SANS}; }}
  .hd-title {{ font-family:{SERIF}; font-size:1.7rem; font-weight:400; letter-spacing:-0.01em; margin:0; color:{GREEN}; }}
  .hd-sub {{ color:{INK2}; font-size:0.9rem; margin-top:2px; }}
  .sec {{ color:{INK3}; font-size:0.72rem; font-weight:700; letter-spacing:0.14em; text-transform:uppercase;
          margin:1.1rem 0 0.5rem 0; }}
  .kpi {{ background:{PANEL}; border:1px solid {LINE}; border-radius:14px; padding:12px 14px 4px 14px; }}
  .kpi .lab {{ font-size:0.78rem; font-weight:600; opacity:0.85; }}
  .kpi .val {{ font-family:{SERIF}; font-size:1.9rem; font-weight:400; letter-spacing:-0.01em; line-height:1.1; margin-top:2px; }}
  .kpi .chip {{ display:inline-block; font-size:0.74rem; font-weight:700; padding:1px 8px; border-radius:999px; margin-top:4px; }}
  .kpi .ctx {{ font-size:0.72rem; margin-left:6px; opacity:0.7; }}
  .card {{ background:{PANEL}; border:1px solid {LINE}; border-left:5px solid {NAVY}; border-radius:14px;
           padding:12px 16px; margin-bottom:10px; }}
  .card.top {{ background:{GREEN}; border-color:{GREEN}; }}
  .card.top .ttl, .card.top .big, .card.top .act, .card.top .rank {{ color:{CREAM} !important; }}
  .card.top .body {{ color:#D9DDD0 !important; }}
  .card.top .act b, .card.top .tag {{ color:{LIME} !important; }}
  .card .tag {{ font-size:0.68rem; font-weight:800; letter-spacing:0.12em; }}
  .card .ttl {{ font-size:1.02rem; font-weight:700; margin:2px 0 0 0; }}
  .card .big {{ font-family:{SERIF}; font-size:1.7rem; font-weight:400; letter-spacing:-0.01em; margin:2px 0 4px 0; }}
  .card .body {{ color:{INK2}; font-size:0.86rem; line-height:1.4; }}
  .card .act {{ color:{INK}; font-size:0.84rem; margin-top:6px; }}
  .card .act b {{ color:{BRICK}; }}
  .rank {{ color:{INK3}; font-size:0.78rem; font-weight:800; margin-right:8px; }}
  div[data-testid="stButton"] > button {{ background:{PANEL}; color:{GREEN}; border:1px solid {LINE}; border-radius:999px;
                                         padding:2px 12px; font-size:0.8rem; font-weight:600; }}
  div[data-testid="stButton"] > button:hover {{ border-color:{GREEN}; background:{GREEN}; color:{CREAM}; }}
  [data-testid="stDataFrame"] {{ border:1px solid {LINE}; border-radius:12px; background:{PANEL}; }}
  .drill {{ background:{PANEL}; border:1px solid {LINE}; border-radius:14px; padding:16px 18px; margin-top:8px; }}
  .drill .claim {{ font-family:{SERIF}; font-size:1.35rem; font-weight:400; }}
  .stMultiSelect [data-baseweb="select"] > div, .stSelectbox [data-baseweb="select"] > div {{
      background:{PANEL}; border-color:{LINE}; border-radius:999px; }}
  span[data-tag], span[data-baseweb="tag"] {{ background:{LIME} !important; border-radius:999px; }}
  span[data-tag] *, span[data-baseweb="tag"] * {{ color:{INK} !important; fill:{INK} !important; }}
  [data-baseweb="menu"] [aria-selected="true"], [data-baseweb="menu"] li:hover {{ background:{PANEL2} !important; }}
</style>
""", unsafe_allow_html=True)

PLOT = dict(template="plotly_white", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=INK2, size=12, family="dm-sans, Helvetica Neue, Arial, sans-serif"),
            margin=dict(l=6, r=6, t=8, b=6),
            xaxis=dict(showgrid=False, zeroline=False), yaxis=dict(gridcolor="#E6E2D6", zeroline=False),
            legend=dict(orientation="h", y=1.08, x=0, font=dict(size=11)), hoverlabel=dict(font_size=12))
NOBAR = {"displayModeBar": False}

money = lambda x: f"${x:,.0f}"
money_k = lambda x: (f"${x/1000:,.1f}k" if abs(x) >= 1000 else f"${x:,.0f}")
pct = lambda x: f"{(x or 0) * 100:.0f}%"


def chip(cur, prev, invert=False, fmt="{:+.0%}"):
    if not prev:
        return f'<span class="chip" style="background:{PANEL2};color:{INK2}">new</span>'
    chg = (cur - prev) / prev
    good = (chg >= 0) != invert
    col = GREEN if good else BRICK
    return f'<span class="chip" style="background:{col}22;color:{col}">{fmt.format(chg)}</span>'


def spark(series: pd.Series, color=BLUE, height=46):
    lo, hi = float(series.min()), float(series.max())
    pad = (hi - lo) * 0.25 or (abs(hi) * 0.1 or 1)
    fig = go.Figure(go.Scatter(y=series.values, x=series.index, mode="lines", line=dict(color=color, width=2, shape="spline"),
                               fill="tozeroy", fillcolor=f"{color}22", hoverinfo="skip"))
    fig.add_trace(go.Scatter(y=[series.values[-1]], x=[series.index[-1]], mode="markers",
                             marker=dict(color=color, size=6), hoverinfo="skip"))
    fig.update_layout(**{**PLOT, "margin": dict(l=4, r=4, t=2, b=0), "height": height, "showlegend": False,
                         "xaxis": dict(visible=False), "yaxis": dict(visible=False, range=[lo - pad, hi + pad])})
    return fig


# ------------------------------------------------------------------ data
@st.cache_data(ttl=1800, show_spinner="Loading warehouse…")
def get_data():
    return m.load()


@st.cache_data(ttl=1800, show_spinner="Computing findings…")
def get_findings(asof, keys):
    return insights.compute(get_data(), asof, list(keys) if keys else None)


@st.cache_data(ttl=1800, show_spinner=False)
def get_frames(asof, keys):
    """Everything the top of the page needs, computed once per filter."""
    d = get_data()
    keys = list(keys) if keys else None
    today = asof.date()
    wk12 = m.pulse(d, (asof - timedelta(weeks=12)).date(), today, keys, grain="week")
    wk12 = wk12[(wk12.bucket < asof.to_period("W-SUN").start_time) &
                (wk12.bucket > (asof - timedelta(weeks=12)).to_period("W-SUN").start_time)]   # whole weeks only
    day8 = m.pulse(d, (asof - timedelta(weeks=8)).date(), today, keys, grain="day")
    k7 = m.kpis(d, today - timedelta(days=6), today, keys)
    k7p = m.kpis(d, today - timedelta(days=13), today - timedelta(days=7), keys)
    fin = m.rebooking(d, (asof - timedelta(weeks=12)).date(), today, keys)
    fin["week"] = m._grain(fin.appointment_date, "week")
    rb_week = fin.groupby("week").rebooked.mean()
    rb_week = rb_week[(rb_week.index < asof.to_period("W-SUN").start_time) &
                      (rb_week.index > (asof - timedelta(weeks=12)).to_period("W-SUN").start_time)]
    rb4 = fin[fin.appointment_date > asof - timedelta(weeks=4)].rebooked.mean()
    rb4p = fin[(fin.appointment_date > asof - timedelta(weeks=8)) & (fin.appointment_date <= asof - timedelta(weeks=4))].rebooked.mean()
    cad = m.cadence(d, keys, today=asof)
    od = cad[cad.overdue]
    mom = m.momentum(d, keys, weeks=4, today=asof)
    rb_loc = m.rebooking_summary(fin)
    cn = m.cancellations(d, (asof - timedelta(weeks=4)).date(), today, keys)
    cs = m.cancel_summary(cn) if len(cn) else pd.DataFrame({"location_name": pd.Series(dtype="str"), "canceled": pd.Series(dtype="float"),
                                                           "lost_value": pd.Series(dtype="float")})
    appts4 = m.for_locations(m.in_range(d.appointments, "appointment_date", (asof - timedelta(weeks=4)).date(), today), keys)
    ar = appts4.groupby("location_name").size().rename("appts4")
    newc = m.for_locations(m.in_range(d.customers, "created_date", (asof - timedelta(weeks=4)).date(), today), keys)
    nc = newc.groupby("location_name").size().rename("new4")
    league = (mom.set_index("location_name")
              .join(rb_loc.set_index("location_name")[["rebook_rate", "visits"]])
              .join(cs.set_index("location_name")[["canceled", "lost_value"]])
              .join(ar).join(nc)
              .join(od.groupby("location_name").agg(overdue=("customer_id", "size"), overdue_value=("avg_ticket", "sum"))))
    for c_ in league.columns:                      # empty joins can come back as strings under pandas 3
        league[c_] = pd.to_numeric(league[c_], errors="coerce")
    league["cancel_rate"] = league.canceled / league.appts4.replace(0, np.nan)
    league = league.fillna({"canceled": 0, "lost_value": 0, "overdue": 0, "overdue_value": 0, "new4": 0, "appts4": 0})
    league = league.reset_index().sort_values("revenue_cur", ascending=False)
    locs = d.locations.set_index("location_name")[["location_key", "state", "region", "lat", "lng"]]
    league = league.join(locs, on="location_name")
    return dict(wk12=wk12, day8=day8, k7=k7, k7p=k7p, rb_week=rb_week, rb4=rb4, rb4p=rb4p, od=od, league=league)


d = get_data()
loc_df = d.locations.sort_values("location_name")
# "Today" = the last *complete* day in the warehouse (the sync day itself is partial).
asof = pd.Timestamp(d.payments.payment_date.max()).normalize() - pd.Timedelta(days=1)
today = asof.date()

# ------------------------------------------------------------------ header + scope
h1, h2, h3 = st.columns([6, 2, 2])
with h1:
    logo_html = f'<img src="data:image/png;base64,{LOGO}" style="height:34px;vertical-align:middle;margin-right:14px">' if LOGO else ""
    st.markdown(f'<div style="display:flex;align-items:center">{logo_html}<p class="hd-title">HDHQ Command Center</p></div>'
                f'<p class="hd-sub">Data through <b>{asof:%a %b %d, %Y}</b> · tiles: last 7 days vs prior 7 · findings: last 12 months</p>', unsafe_allow_html=True)
with h2:
    regions = sorted(loc_df.region.dropna().unique())
    sel_regions = st.multiselect("Region", regions, default=[], placeholder="All regions", label_visibility="collapsed")
with h3:
    opts = loc_df[loc_df.region.isin(sel_regions)] if sel_regions else loc_df
    sel_locs = st.multiselect("Locations", opts.location_name.tolist(), default=[], placeholder="All locations",
                              label_visibility="collapsed")
keys = opts[opts.location_name.isin(sel_locs)].location_key.tolist() if sel_locs else opts.location_key.tolist()
if not sel_regions and not sel_locs:
    keys = None
ktuple = tuple(keys) if keys else None

F = get_frames(asof, ktuple)
findings = get_findings(asof, ktuple)
if not findings:
    st.info("Not enough history in this selection to compute findings — widen the filter.")
k7, k7p, wk12 = F["k7"], F["k7p"], F["wk12"].set_index("bucket")

# ------------------------------------------------------------------ KPI strip
st.markdown('<div class="sec">This week at a glance</div>', unsafe_allow_html=True)
tiles = [
    ("Revenue collected · 7d", money_k(k7["net_revenue"]), chip(k7["net_revenue"], k7p["net_revenue"]),
     f"prior {money_k(k7p['net_revenue'])}", wk12.net_revenue, BLUE),
    ("Finished visits · 7d", f"{k7['finished']:,}", chip(k7["finished"], k7p["finished"]),
     f"prior {k7p['finished']:,}", wk12.finished, GREEN),
    ("Avg ticket · 7d", money(k7["avg_ticket"]), chip(k7["avg_ticket"], k7p["avg_ticket"]),
     f"prior {money(k7p['avg_ticket'])}", wk12.finished_value / wk12.finished.replace(0, np.nan), PINK),
    ("Rebooked · 4wk", pct(F["rb4"]), chip(F["rb4"], F["rb4p"]), f"prior {pct(F['rb4p'])}",
     F["rb_week"], AMBER),
    ("Cancel rate · 7d", pct(k7["cancel_rate"]), chip(k7["cancel_rate"], k7p["cancel_rate"], invert=True),
     f"prior {pct(k7p['cancel_rate'])}", wk12.cancel_rate, RED),
    ("New customers · 7d", f"{k7['new_customers']:,}", chip(k7["new_customers"], k7p["new_customers"]),
     f"prior {k7p['new_customers']:,}", None, ORANGE),
    ("Overdue · unbooked", f"{len(F['od']):,}", f'<span class="chip" style="background:rgba(255,255,255,0.55);color:{INK}">'
     f'{money_k(F["od"].avg_ticket.sum())} at stake</span>', "", None, AMBER),
]
TILE_BG = [GREEN, LIME, SKY, PANEL, PANEL, PANEL, SALMON]            # revenue tile dark green, then brand accents
TILE_FG = [CREAM, INK, INK, INK, INK, INK, INK]
cols = st.columns(len(tiles))
for i, (col, (lab, val, ch, ctx, series, color)) in enumerate(zip(cols, tiles)):
    bg, fg = TILE_BG[i], TILE_FG[i]
    with col:
        st.markdown(f'<div class="kpi" style="background:{bg};border-color:{bg if bg != PANEL else LINE}">'
                    f'<div class="lab" style="color:{fg}">{lab}</div><div class="val" style="color:{fg}">{val}</div>'
                    f'{ch}<span class="ctx" style="color:{fg}">{ctx}</span></div>', unsafe_allow_html=True)
        if series is not None and len(series) > 2:
            st.plotly_chart(spark(series.fillna(0), GREEN if bg in (GREEN, LIME, SKY, SALMON) else color),
                            use_container_width=True, config=NOBAR)

# ------------------------------------------------------------------ findings + pulse
left, right = st.columns([5, 4], gap="large")
with left:
    st.markdown('<div class="sec">What to act on — ranked by annual $ impact</div>', unsafe_allow_html=True)
    if not findings:
        st.caption("No findings for this selection.")
    for i, f in enumerate(findings[:6], 1):
        tag, color = SEV[f["severity"]]
        scope = f["scope"] if keys is None else ", ".join(sel_locs or sel_regions)
        c1, c2 = st.columns([10, 1.6])
        with c1:
            st.markdown(
                f'<div class="card{" top" if i == 1 else ""}" style="border-left-color:{color}">'
                f'<span class="rank">#{i}</span><span class="tag" style="color:{color}">{tag}</span>'
                f'<span class="tag" style="color:{INK3}"> · {scope}</span>'
                f'<div class="ttl">{f["title"]}</div><div class="big">{f["headline"]}</div>'
                f'<div class="body">{f["body"]}</div>'
                f'<div class="act"><b>Do:</b> {f["action"]}</div></div>', unsafe_allow_html=True)
        with c2:
            st.write("")
            st.write("")
            if st.button("Open", key=f"open_{f['key']}"):
                st.session_state["drill"] = f["key"]

with right:
    st.markdown('<div class="sec">Revenue pulse — last 8 weeks</div>', unsafe_allow_html=True)
    grain = st.radio("grain", ["Daily", "Weekly"], horizontal=True, label_visibility="collapsed")
    p = F["day8"] if grain == "Daily" else F["wk12"]
    xfmt = "%{x|%a %b %d}" if grain == "Daily" else "wk of %{x|%b %d}"
    avg_name = "7-day avg" if grain == "Daily" else "4-week avg"
    fig = go.Figure()
    fig.add_bar(x=p.bucket, y=p.net_revenue, name="Collected", marker_color=GREEN, opacity=0.9,
                hovertemplate=xfmt + "<br>$%{y:,.0f}<extra></extra>")
    fig.add_scatter(x=p.bucket, y=p.revenue_ma, name=avg_name, line=dict(color=SALMON, width=2.5),
                    hovertemplate=avg_name + " $%{y:,.0f}<extra></extra>")
    fig.add_bar(x=p.bucket, y=-p.lost_value, name="Lost to cancels / no-shows", marker_color=BRICK, opacity=0.7,
                customdata=p.lost_value, hovertemplate=xfmt + "<br>lost $%{customdata:,.0f}<extra></extra>")
    fig.update_layout(**{**PLOT, "height": 300, "barmode": "relative", "bargap": 0.2,
                         "yaxis": dict(gridcolor="#E6E2D6", zeroline=True, zerolinecolor=INK3, tickprefix="$", tickformat="~s")})
    st.plotly_chart(fig, use_container_width=True, config=NOBAR)

    st.markdown('<div class="sec">Appointment outcomes — weekly</div>', unsafe_allow_html=True)
    w = F["wk12"]
    fig2 = go.Figure()
    fig2.add_bar(x=w.bucket, y=w.finished, name="Finished", marker_color=SAGE)
    fig2.add_bar(x=w.bucket, y=w.canceled, name="Canceled", marker_color=BRICK)
    fig2.add_bar(x=w.bucket, y=w.no_shows, name="No-show", marker_color=SALMON)
    fig2.update_layout(**{**PLOT, "height": 200, "barmode": "stack", "bargap": 0.25})
    st.plotly_chart(fig2, use_container_width=True, config=NOBAR)

    st.markdown('<div class="sec">Rebooked at checkout — weekly</div>', unsafe_allow_html=True)
    rbw = F["rb_week"]
    tgt = next((x["target"] for x in findings if x["key"] == "rebooking"), None)
    fig3 = go.Figure(go.Scatter(x=rbw.index, y=rbw.values, mode="lines+markers", line=dict(color=GREEN, width=2.5),
                                fill="tozeroy", fillcolor=f"{SAGE}33", name="Rebook rate",
                                hovertemplate="wk of %{x|%b %d}: %{y:.0%}<extra></extra>"))
    if tgt:
        fig3.add_hline(y=tgt, line=dict(color=SAGE, dash="dot"), annotation_text=f"top-quartile location {tgt:.0%}",
                       annotation_font_color=GREEN, annotation_position="bottom right")
    fig3.update_layout(**{**PLOT, "height": 180, "showlegend": False,
                          "yaxis": dict(gridcolor="#E6E2D6", zeroline=False, tickformat=".0%", range=[0, max(0.6, float(rbw.max()) + 0.05)])})
    st.plotly_chart(fig3, use_container_width=True, config=NOBAR)

# ------------------------------------------------------------------ league table + map
st.markdown('<div class="sec">Location league — last 4 weeks vs the 4 before</div>', unsafe_allow_html=True)
L = F["league"]
lt = st.container()
mp = st.container()
with lt:
    show = L[["location_name", "revenue_cur", "revenue_chg", "finished_cur", "finished_chg", "rebook_rate",
              "cancel_rate", "lost_value", "new4", "overdue", "overdue_value"]].copy()
    for c in ("revenue_chg", "finished_chg"):
        show[c] = (show[c].replace([np.inf, -np.inf], np.nan) * 100).round(0)
    show["rebook_rate"] = show.rebook_rate * 100
    show["cancel_rate"] = (show.cancel_rate * 100).round(0).fillna(0)

    def _sign(v):
        if pd.isna(v) or v == 0:
            return f"color:{INK2}"
        return f"color:{GREEN};font-weight:700" if v > 0 else f"color:{BRICK};font-weight:700"

    sty = show.style.map(_sign, subset=["revenue_chg", "finished_chg"])
    sty = sty.map(lambda v: f"color:{BRICK};font-weight:700" if v >= 25 else "", subset=["cancel_rate"])
    st.dataframe(
        sty, hide_index=True, use_container_width=True, height=min(60 + 35 * len(show), 700),
        column_config={
            "location_name": st.column_config.TextColumn("Location", width="medium"),
            "revenue_cur": st.column_config.ProgressColumn("Revenue 4wk", format="$%d", min_value=0,
                                                           max_value=float(show.revenue_cur.max()) if len(show) and show.revenue_cur.max() > 0 else 1.0),
            "revenue_chg": st.column_config.NumberColumn("Δ rev %", format="%+d", help="vs prior 4 weeks"),
            "finished_cur": st.column_config.NumberColumn("Visits", format="%d"),
            "finished_chg": st.column_config.NumberColumn("Δ visits %", format="%+d"),
            "rebook_rate": st.column_config.ProgressColumn("Rebook %", format="%d%%", min_value=0, max_value=100,
                                                           help="finished visits with the next appointment booked within 7 days (12 wks)"),
            "cancel_rate": st.column_config.NumberColumn("Cancel %", format="%d"),
            "lost_value": st.column_config.NumberColumn("Lost $", format="$%d"),
            "new4": st.column_config.NumberColumn("New cust", format="%d"),
            "overdue": st.column_config.NumberColumn("Overdue", format="%d"),
            "overdue_value": st.column_config.NumberColumn("Overdue $", format="$%d"),
        })
    st.caption("Δ columns compare the last 4 weeks with the 4 weeks before. Rebook % uses the last 12 weeks. "
               "Overdue = customers with 2+ visits past their own usual interval and nothing booked.")

with mp:
    st.markdown('<div class="sec">Where it is happening</div>', unsafe_allow_html=True)
    mc1, mc2 = st.columns([3, 2])
    layer = mc2.radio("Customers", ["Heat", "Pins", "Both"], horizontal=True, label_visibility="collapsed")
    metric = mc1.selectbox("Map metric", ["Revenue 4wk", "Growth (Δ rev)", "Finished visits", "Lost to cancels $",
                                         "Rebook %", "Overdue customers", "Overdue $", "New customers"],
                          label_visibility="collapsed")
    col = {"Revenue 4wk": "revenue_cur", "Growth (Δ rev)": "revenue_chg", "Finished visits": "finished_cur",
           "Lost to cancels $": "lost_value", "Rebook %": "rebook_rate", "Overdue customers": "overdue",
           "Overdue $": "overdue_value", "New customers": "new4"}[metric]
    g = L[L.lat.notna()].copy()
    v = g[col].replace([np.inf, -np.inf], np.nan).fillna(0)
    diverging = col == "revenue_chg"
    bad_high = col in ("lost_value", "overdue", "overdue_value")
    vmax = float(v.abs().max()) if len(v) and v.abs().max() > 0 else 1.0
    size = 14 + 34 * (v.abs() / vmax)
    GREENS = [[0, "#DDE797"], [0.5, "#8CA563"], [1, "#3C4E39"]]
    REDS = [[0, "#F2A094"], [1, "#B8493D"]]
    DIVERGE = [[0, "#B8493D"], [0.5, "#F4F2EC"], [1, "#3C4E39"]]
    scale = DIVERGE if diverging else (REDS if bad_high else GREENS)
    fig = go.Figure()
    cg = d.customers[d.customers.lat.notna() & d.customers.lng.notna()].copy()
    if keys:
        cg = cg[cg.location_key.isin(keys)]
    if layer in ("Heat", "Both"):
        fig.add_densitymap(lat=cg.lat, lon=cg.lng, radius=10, colorscale=[[0, "rgba(37,61,78,0)"], [1, "rgba(37,61,78,0.45)"]],
                           showscale=False, hoverinfo="skip", name="Customers")
    if layer in ("Pins", "Both"):
        od_ids = set(F["od"].customer_id)
        cg["overdue"] = cg.id.isin(od_ids)
        cg["name"] = (cg.first_name.fillna("") + " " + cg.last_name.fillna("")).str.strip()
        cg["addr"] = (cg.address1.fillna("") + ", " + cg.city.fillna("") + " " + cg.state.fillna("") + " " + cg.postal_code.fillna("")).str.strip(", ")
        for flag, colr, sz, nm in ((False, NAVY, 5, "Customer"), (True, BRICK, 7, "Overdue customer")):
            sub = cg[cg.overdue == flag]
            fig.add_scattermap(lat=sub.lat, lon=sub.lng, mode="markers", name=nm,
                               marker=dict(size=sz, color=colr, opacity=0.55 if not flag else 0.9),
                               customdata=np.stack([sub.name, sub.addr, sub.location_name.fillna("")], axis=1),
                               hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>%{customdata[2]}<extra>" + nm + "</extra>")
    marker = dict(size=size, color=v, colorscale=scale, showscale=True, opacity=0.92,
                  colorbar=dict(title=dict(text=metric, font=dict(size=11)), thickness=10, len=0.5, y=0.25,
                                tickfont=dict(size=10), tickformat=".0%" if col in ("revenue_chg", "rebook_rate") else "~s"))
    if diverging:
        marker["cmid"] = 0
    fig.add_scattermap(
        lat=g.lat, lon=g.lng, mode="markers+text", text=g.location_name, textposition="top center",
        textfont=dict(color=INK, size=11, family="dm-sans"), marker=marker,
        customdata=np.stack([g.revenue_cur, g.revenue_chg.fillna(0), g.finished_cur, g.rebook_rate.fillna(0),
                             g.cancel_rate.fillna(0), g.lost_value, g.overdue, g.overdue_value, g.new4], axis=1),
        hovertemplate="<b>%{text}</b><br>Revenue 4wk $%{customdata[0]:,.0f} (%{customdata[1]:+.0%})"
                      "<br>Visits %{customdata[2]:,.0f} · Rebook %{customdata[3]:.0%} · Cancel %{customdata[4]:.0%}"
                      "<br>Lost to cancels $%{customdata[5]:,.0f}<br>Overdue %{customdata[6]:,.0f} cust · $%{customdata[7]:,.0f}"
                      "<br>New customers %{customdata[8]:,.0f}<extra></extra>")
    fig.update_layout(map=dict(style="carto-positron", center=dict(lat=38.3, lon=-96), zoom=3.7),
                      height=520, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)", showlegend=False,
                      font=dict(color=INK2))
    st.plotly_chart(fig, use_container_width=True,
                    config={"scrollZoom": True, "displayModeBar": True, "displaylogo": False,
                            "modeBarButtonsToRemove": ["toImage", "select2d", "lasso2d"]})
    st.caption("Bubble size & colour = selected metric. Customers: heat = where they live, pins = each address (red = overdue, nothing booked). Scroll or pinch to zoom, drag to pan, double-click to reset.")

# ------------------------------------------------------------------ drill-down
drill = st.session_state.get("drill")
if drill:
    f = next((x for x in findings if x["key"] == drill), None)
    if f:
        st.markdown('<div class="sec">Drill-down</div>', unsafe_allow_html=True)
        tag, color = SEV[f["severity"]]
        st.markdown(f'<div class="drill"><span class="tag" style="color:{color}">{tag}</span>'
                    f'<div class="claim">{f["title"]} — {f["headline"]}</div>'
                    f'<div class="body" style="color:{INK2};margin-top:4px">{f["body"]}</div>'
                    f'<div class="act" style="margin-top:8px"><b style="color:{BRICK}">Do:</b> {f["action"]}</div></div>',
                    unsafe_allow_html=True)
        ev = f["evidence"]
        pc, tc = st.columns([5, 5], gap="large")
        with pc:
            if drill == "rebooking":
                e = ev.sort_values("rebook_rate")
                fig = go.Figure(go.Bar(x=e.rebook_rate, y=e.location_name, orientation="h",
                                       marker_color=[SAGE if r >= f["target"] else NAVY for r in e.rebook_rate],
                                       text=[f"{r:.0%}" for r in e.rebook_rate], textposition="outside",
                                       customdata=e.opportunity,
                                       hovertemplate="%{y}: %{x:.0%} · $%{customdata:,.0f}/yr gap<extra></extra>"))
                fig.add_vline(x=f["target"], line=dict(color=BRICK, dash="dot"),
                              annotation_text=f"top-quartile {f['target']:.0%}", annotation_font_color=BRICK)
                fig.update_layout(**{**PLOT, "height": 30 * len(e) + 60, "xaxis": dict(tickformat=".0%", showgrid=False)})
                st.plotly_chart(fig, use_container_width=True, config=NOBAR)
            elif drill == "cancels":
                s = f["summary"].sort_values("lost_value")
                fig = go.Figure(go.Bar(x=s.lost_value, y=s.location_name, orientation="h", marker_color=RED,
                                       text=[money_k(v) for v in s.lost_value], textposition="outside",
                                       customdata=np.stack([s.canceled, s.last_minute_share], axis=1),
                                       hovertemplate="%{y}: $%{x:,.0f} · %{customdata[0]} cancels · "
                                                     "%{customdata[1]:.0%} under 24h<extra></extra>"))
                fig.update_layout(**{**PLOT, "height": 30 * len(s) + 60,
                                     "xaxis": dict(tickprefix="$", tickformat="~s", showgrid=False)})
                st.plotly_chart(fig, use_container_width=True, config=NOBAR)
            else:
                spec = {"leakage": ("location_name", "one_and_done", ".0%", RED),
                        "decline": ("location_name", "chg", "+.0%", None),
                        "tips": ("location_name", "tip_rate", ".0%", BLUE),
                        "sources": ("acq", "visit_conversion", ".0%", BLUE),
                        "capacity": ("staff_name", "utilization", ".0%", BLUE),
                        "overdue": ("location_name", "value", "$,.0f", SAGE)}[drill]
                ycol, xcol, fmt, color_ = spec
                if drill == "overdue":
                    e = ev.groupby("location_name").agg(n=("customer_id", "size"), value=("avg_ticket", "sum")).reset_index()
                else:
                    e = ev.copy()
                e = e.sort_values(xcol).tail(25)
                colors = color_ if color_ else [BRICK if v_ < 0 else SAGE for v_ in e[xcol]]
                fig = go.Figure(go.Bar(x=e[xcol], y=e[ycol], orientation="h", marker_color=colors,
                                       text=[f"{v_:{fmt}}" for v_ in e[xcol]], textposition="outside"))
                fig.update_layout(**{**PLOT, "height": 28 * len(e) + 60,
                                     "xaxis": dict(tickformat=fmt.replace("$", ""), showgrid=False)})
                st.plotly_chart(fig, use_container_width=True, config=NOBAR)
        with tc:
            st.markdown(f"**Action list** — {len(ev):,} rows")
            st.dataframe(ev, hide_index=True, use_container_width=True, height=min(60 + 35 * len(ev), 520))
            st.download_button("Export CSV", ev.to_csv(index=False).encode(),
                               file_name=f"hdhq_{drill}_{asof:%Y%m%d}.csv", mime="text/csv")
        if st.button("Close drill-down"):
            st.session_state.pop("drill", None)
            st.rerun()
