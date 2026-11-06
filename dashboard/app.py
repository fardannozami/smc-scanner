import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta, timezone

from database.db_manager import (
    init_db, get_all_trades, get_active_setups,
    get_risk_log, get_journal_entries, save_journal_entry,
    log_trade, close_trade, get_open_paper_trades, get_paper_trades,
    get_strategy_stats, get_paper_overall_stats, get_paper_period_stats,
    recompute_strategy_stats,
)
from scanner.news_filter import get_upcoming_events, get_next_high_impact
import config

st.set_page_config(
    page_title="Trading OS",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.6rem; }
.impact-high   { color: #ef5350; font-weight: 700; }
.impact-medium { color: #ffd54f; font-weight: 700; }
.impact-low    { color: #69f0ae; }
.countdown-box {
    background: #1e1e2e;
    border: 1px solid #ef5350;
    border-radius: 8px;
    padding: 14px 20px;
    margin-bottom: 12px;
}
</style>
""", unsafe_allow_html=True)

GRADE_EMOJI = {"ELITE": "💎", "A+": "🥇", "A": "🥈", "Standard": "🥉"}
BIAS_ICON = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
IMPACT_COLOR = {"High": "#ef5350", "Medium": "#ffd54f", "Low": "#69f0ae"}


def fmt_countdown(minutes: int) -> str:
    if minutes < 0:
        return f"{abs(minutes)}m ago"
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m"


def main():
    init_db()

    with st.sidebar:
        st.title("📊 Trading OS")
        st.caption(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        st.divider()
        page = st.radio(
            "Navigation",
            ["Dashboard", "Economic Calendar", "Active Setups",
             "Paper Trades", "Trade Journal", "Analytics", "Daily Review",
             "Risk Status"],
            label_visibility="collapsed",
        )
        st.divider()
        st.caption(f"Pairs: {', '.join(config.SYMBOLS)}")
        st.caption(f"TF: {config.HTF} → {config.MTF} → {config.LTF}")
        news_state = "✅ ON" if config.NEWS_FILTER_ENABLED else "❌ OFF"
        st.caption(f"News filter: {news_state}")
        if st.button("🔄 Refresh", width="stretch"):
            st.rerun()

    if page == "Dashboard":
        show_dashboard()
    elif page == "Economic Calendar":
        show_calendar()
    elif page == "Active Setups":
        show_active_setups()
    elif page == "Paper Trades":
        show_paper_trades()
    elif page == "Trade Journal":
        show_journal()
    elif page == "Analytics":
        show_analytics()
    elif page == "Daily Review":
        show_daily_review()
    elif page == "Risk Status":
        show_risk_status()


# ─────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────
def show_dashboard():
    st.header("Dashboard")
    today_utc = datetime.now(timezone.utc).date()
    today = get_paper_period_stats(today_utc)
    week = get_paper_period_stats(today_utc - timedelta(days=7))

    d_total = today["total"]
    w_total = week["total"]
    d_decided = today["wins"] + today["losses"]
    w_decided = week["wins"] + week["losses"]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Today Signals", d_total)
    c2.metric(
        "Today Win Rate",
        f"{today['wins']/d_decided*100:.0f}%" if d_decided else "—",
    )
    c3.metric("Today Total R", f"{today['total_r']:+.2f}" if d_total else "—")
    c4.metric("Week Signals", w_total)
    c5.metric(
        "Week Win Rate",
        f"{week['wins']/w_decided*100:.0f}%" if w_decided else "—",
    )

    st.divider()

    # Next high-impact event banner
    next_ev = get_next_high_impact()
    if next_ev:
        mins = next_ev["minutes_away"]
        color = "#ef5350" if mins <= 30 else "#ffd54f" if mins <= 60 else "#4fc3f7"
        st.markdown(
            f"<div class='countdown-box'>"
            f"⏰ <b>Next High-Impact Event</b>: "
            f"<span style='color:{color}'>{next_ev['currency']} — {next_ev['title']}</span> "
            f"&nbsp;|&nbsp; <b style='color:{color}'>{fmt_countdown(mins)}</b>"
            f"</div>",
            unsafe_allow_html=True,
        )

    col_left, col_right = st.columns([3, 1])

    with col_left:
        st.subheader("Recent Setups")
        setups = get_active_setups(8)
        if setups:
            for s in setups:
                g = s.get("grade", "Standard")
                st.write(
                    f"{GRADE_EMOJI.get(g,'📊')} **{s['symbol']}** | {g} (Score {s['score']}) | "
                    f"{BIAS_ICON.get(s.get('bias','neutral'),'⚪')} {s.get('bias','')} | "
                    f"{s.get('session','')} | {str(s.get('detected_at',''))[:16]}"
                )
        else:
            st.info("No setups detected yet. Start the scanner.")

    with col_right:
        st.subheader("Constitution")
        st.write(f"Max risk/trade: **{config.MAX_RISK_PER_TRADE}%**")
        st.write(f"Daily DD limit: **{config.MAX_DAILY_DRAWDOWN}%**")
        st.write(f"Weekly DD limit: **{config.MAX_WEEKLY_DRAWDOWN}%**")
        st.write(f"Max consec. losses: **{config.MAX_CONSECUTIVE_LOSSES}**")
        st.write(f"News block pre: **{config.NEWS_BLOCK_MINUTES_BEFORE}m**")
        st.write(f"News block post: **{config.NEWS_BLOCK_MINUTES_AFTER}m**")

        st.divider()
        st.subheader("Paper Win Rate")
        recompute_strategy_stats()
        paper_stats = get_strategy_stats()
        if paper_stats:
            for row in paper_stats:
                st.write(
                    f"**{row['strategy']}**: {(row['win_rate'] or 0):.1f}% "
                    f"({row['wins'] or 0}W / {row['losses'] or 0}L)"
                )
            overall = get_paper_overall_stats()
            st.caption(
                f"Overall: {overall['win_rate']:.1f}% "
                f"· {overall['total_r']:+.2f}R · {overall['open_trades']} open"
            )
        else:
            st.caption("No paper trades yet.")

    risk_log = get_risk_log(50)
    if risk_log:
        st.divider()
        st.subheader("Equity / Drawdown")
        df_r = pd.DataFrame(risk_log).sort_values("logged_at")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_r["logged_at"], y=df_r["equity"],
                                  name="Equity", line=dict(color="#26a69a")))
        fig.add_trace(go.Scatter(x=df_r["logged_at"], y=df_r["daily_dd"],
                                  name="Daily DD %", yaxis="y2",
                                  line=dict(color="#ef5350", dash="dash")))
        fig.update_layout(
            yaxis2=dict(overlaying="y", side="right", title="DD %"),
            height=280, margin=dict(l=0, r=0, t=20, b=0),
            legend=dict(orientation="h"),
        )
        st.plotly_chart(fig, width="stretch")


# ─────────────────────────────────────────────
# Economic Calendar
# ─────────────────────────────────────────────
def show_calendar():
    st.header("Economic Calendar")
    st.caption("Source: ForexFactory — refreshed hourly. High-impact events trigger news blackout in the scanner.")

    col1, col2, col3 = st.columns(3)
    hours = col1.slider("Look-ahead (hours)", 6, 72, 24, step=6)
    impact_sel = col2.multiselect("Impact", ["High", "Medium", "Low"], default=["High", "Medium"])
    tracked = col3.multiselect(
        "Currency filter",
        ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"],
        default=["USD", "EUR", "GBP", "JPY"],
    )

    events = get_upcoming_events(hours=hours, impacts=tuple(impact_sel))
    if tracked:
        events = [e for e in events if e["currency"].upper() in tracked]

    if not events:
        st.info("No events found for the selected filters.")
        return

    # Next high-impact countdown card
    high_events = [e for e in events if e["impact"] == "High" and e["minutes_away"] > 0]
    if high_events:
        nxt = high_events[0]
        mins = nxt["minutes_away"]
        urgency = "#ef5350" if mins <= 30 else "#ffd54f" if mins <= 60 else "#4fc3f7"
        st.markdown(
            f"<div class='countdown-box'>"
            f"⏰ <b>Next HIGH-IMPACT</b>: "
            f"<span style='color:{urgency}'>{nxt['currency']} — {nxt['title']}</span>"
            f"&nbsp;&nbsp;<b style='color:{urgency}; font-size:1.3rem'>{fmt_countdown(mins)}</b>"
            f"<br><small style='color:#787b86'>Forecast: {nxt['forecast']} &nbsp;|&nbsp; "
            f"Previous: {nxt['previous']}</small>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Build display table
    rows = []
    now_utc = datetime.now(timezone.utc)
    for ev in events:
        local_str = ev["datetime_utc"].strftime("%a %d %b  %H:%M UTC")
        mins = ev["minutes_away"]
        if mins < 0:
            cd = f"{abs(mins)}m ago"
        elif mins == 0:
            cd = "🔴 NOW"
        else:
            cd = fmt_countdown(mins)

        blackout = (
            ev["impact"] == "High"
            and -config.NEWS_BLOCK_MINUTES_AFTER <= mins <= config.NEWS_BLOCK_MINUTES_BEFORE
        )
        rows.append({
            "Time (UTC)": local_str,
            "Countdown": cd,
            "Currency": ev["currency"],
            "Impact": ev["impact"],
            "Event": ev["title"],
            "Forecast": ev["forecast"],
            "Previous": ev["previous"],
            "Actual": ev["actual"],
            "Scanner": "🚫 BLOCKED" if blackout else "✅ clear",
        })

    df = pd.DataFrame(rows)

    def color_impact(val):
        colors = {"High": "color: #ef5350; font-weight:700",
                  "Medium": "color: #ffd54f; font-weight:600",
                  "Low": "color: #69f0ae"}
        return colors.get(val, "")

    def color_scanner(val):
        return "color: #ef5350; font-weight:700" if "BLOCKED" in str(val) else "color: #69f0ae"

    styled = df.style.map(color_impact, subset=["Impact"]) \
                     .map(color_scanner, subset=["Scanner"])
    st.dataframe(styled, width="stretch", hide_index=True)

    # Timeline chart
    st.divider()
    st.subheader("Timeline")
    if rows:
        df_chart = pd.DataFrame([
            {
                "Event": f"{r['Currency']} — {r['Event'][:40]}",
                "Time": events[i]["datetime_utc"],
                "Impact": r["Impact"],
            }
            for i, r in enumerate(rows)
        ])
        color_map = {"High": "#ef5350", "Medium": "#ffd54f", "Low": "#69f0ae"}
        fig = px.scatter(
            df_chart, x="Time", y="Event", color="Impact",
            color_discrete_map=color_map,
            title="Upcoming Events Timeline",
        )
        fig.add_vline(x=now_utc, line_dash="dash", line_color="#4fc3f7",
                      annotation_text="Now", annotation_position="top right")
        fig.update_layout(height=max(300, len(rows) * 30 + 80),
                          margin=dict(l=0, r=0, t=40, b=0),
                          yaxis=dict(tickfont=dict(size=10)))
        st.plotly_chart(fig, width="stretch")


# ─────────────────────────────────────────────
# Active Setups
# ─────────────────────────────────────────────
def show_active_setups():
    st.header("Active Setups")
    setups = get_active_setups(50)
    if not setups:
        st.info("No setups in database yet.")
        return

    df = pd.DataFrame(setups)
    grades = df["grade"].dropna().unique().tolist()
    selected = st.multiselect("Grade filter", ["ELITE", "A+", "A", "Standard"], default=grades)
    df = df[df["grade"].isin(selected)]

    if df.empty:
        st.info("No setups match the filter.")
        return

    cols = ["detected_at", "symbol", "timeframe", "bias", "grade", "score", "entry", "sl", "tp1", "session"]
    cols = [c for c in cols if c in df.columns]
    st.dataframe(df[cols].reset_index(drop=True), width="stretch")

    if st.checkbox("Show chart screenshots"):
        for s in setups[:5]:
            cp = s.get("chart_path")
            if cp and os.path.exists(cp):
                st.image(cp, caption=f"{s['symbol']} — {s['grade']} (Score {s['score']})",
                         width="stretch")


# ─────────────────────────────────────────────
# Trade Journal
# ─────────────────────────────────────────────
def show_journal():
    st.header("Trade Journal")
    tab1, tab2 = st.tabs(["Log Trade", "View Trades"])

    with tab1:
        with st.form("log_trade_form"):
            c1, c2 = st.columns(2)
            symbol = c1.selectbox("Symbol", config.SYMBOLS)
            direction = c2.radio("Direction", ["Buy", "Sell"], horizontal=True)
            c3, c4, c5 = st.columns(3)
            entry = c3.number_input("Entry", format="%.5f", value=0.0)
            sl = c4.number_input("Stop Loss", format="%.5f", value=0.0)
            tp = c5.number_input("Take Profit", format="%.5f", value=0.0)
            grade = st.selectbox("Setup Grade", ["Standard", "A", "A+", "ELITE"])
            notes = st.text_area("Notes", height=80)
            if st.form_submit_button("Log Trade", type="primary"):
                if min(entry, sl, tp) <= 0:
                    st.error("Entry, stop loss and take profit must be greater than zero.")
                elif not (sl < entry < tp or tp < entry < sl):
                    st.error("Levels are inconsistent with the selected direction.")
                else:
                    tid = log_trade(
                        symbol, direction, entry, sl, tp,
                        grade=grade, notes=notes,
                    )
                    st.success(f"Trade #{tid} logged.")

    with tab2:
        trades = get_all_trades()
        if trades:
            df = pd.DataFrame(trades)
            st.dataframe(df, width="stretch")
            st.divider()
            st.subheader("Close a Trade")
            with st.form("close_trade_form"):
                open_ids = [t["id"] for t in trades if not t.get("outcome")]
                if open_ids:
                    tid = st.selectbox("Trade ID", open_ids)
                    exit_p = st.number_input("Exit Price", format="%.5f", min_value=0.0)
                    outcome = st.radio("Outcome", ["win", "loss", "breakeven"], horizontal=True)
                    rr = st.number_input("RR Achieved", value=0.0, step=0.1)
                    close_notes = st.text_input("Notes")
                    if st.form_submit_button("Close Trade"):
                        if exit_p <= 0:
                            st.error("Exit price must be greater than zero.")
                        else:
                            close_trade(tid, exit_p, outcome, rr, close_notes)
                            st.success(f"Trade #{tid} closed as {outcome}.")
                            st.rerun()
                else:
                    st.info("No open trades to close.")
        else:
            st.info("No trades logged yet.")


# ─────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────
def show_analytics():
    st.header("Performance Analytics")
    trades = get_all_trades()
    if not trades:
        st.info("No trade data yet.")
        return

    df = pd.DataFrame(trades)
    df["opened_at"] = pd.to_datetime(df["opened_at"])

    c1, c2 = st.columns(2)
    with c1:
        if df["outcome"].notna().any():
            oc = df["outcome"].value_counts()
            fig = px.pie(values=oc.values, names=oc.index, title="Win / Loss / Breakeven",
                         color_discrete_map={"win": "#26a69a", "loss": "#ef5350", "breakeven": "#ffd54f"})
            st.plotly_chart(fig, width="stretch")
    with c2:
        if df["outcome"].notna().any() and "grade" in df.columns:
            g_df = df[df["outcome"].notna()].groupby(["grade", "outcome"]).size().reset_index(name="count")
            fig = px.bar(g_df, x="grade", y="count", color="outcome", title="Outcomes by Grade",
                         color_discrete_map={"win": "#26a69a", "loss": "#ef5350", "breakeven": "#ffd54f"})
            st.plotly_chart(fig, width="stretch")

    if df["rr_achieved"].notna().any():
        fig = px.histogram(df[df["rr_achieved"].notna()], x="rr_achieved",
                           title="RR Distribution", nbins=20,
                           color_discrete_sequence=["#2196F3"])
        st.plotly_chart(fig, width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        if df["outcome"].notna().any():
            sym_df = df[df["outcome"].notna()].groupby(["symbol", "outcome"]).size().reset_index(name="count")
            fig = px.bar(sym_df, x="symbol", y="count", color="outcome", title="By Symbol",
                         color_discrete_map={"win": "#26a69a", "loss": "#ef5350", "breakeven": "#ffd54f"})
            st.plotly_chart(fig, width="stretch")
    with c4:
        if "session" in df.columns and df["outcome"].notna().any():
            ses_df = df[df["outcome"].notna()].groupby(["session", "outcome"]).size().reset_index(name="count")
            fig = px.bar(ses_df, x="session", y="count", color="outcome", title="By Session",
                         color_discrete_map={"win": "#26a69a", "loss": "#ef5350", "breakeven": "#ffd54f"})
            st.plotly_chart(fig, width="stretch")

    # Equity curve from trades
    if df["rr_achieved"].notna().any():
        df_sorted = df[df["rr_achieved"].notna()].sort_values("opened_at")
        df_sorted["cumulative_rr"] = df_sorted["rr_achieved"].cumsum()
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_sorted["opened_at"], y=df_sorted["cumulative_rr"],
            fill="tozeroy", name="Cumulative RR",
            line=dict(color="#26a69a"),
        ))
        fig.update_layout(title="Cumulative RR Curve", height=300,
                          margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig, width="stretch")


# ─────────────────────────────────────────────
# Daily Review
# ─────────────────────────────────────────────
def show_daily_review():
    st.header("Daily Trading Review")
    st.caption("End-of-day reflection — stored for future AI coaching analysis.")

    trades = get_all_trades()
    closed = [t for t in trades if t.get("outcome")]

    with st.form("daily_review_form"):
        trade_id = None
        if closed:
            options = {f"#{t['id']} — {t['symbol']} {t['direction']} ({t['outcome']})": t["id"]
                       for t in closed[:20]}
            sel = st.selectbox("Link to trade (optional)", ["None"] + list(options.keys()))
            trade_id = options.get(sel)

        followed_plan = st.radio(
            "Did you follow your trading plan?",
            ["Yes — fully", "Yes — mostly", "No — deviated", "No trades today"],
            horizontal=True,
        )
        outcome = st.radio(
            "Overall outcome today?",
            ["Profitable", "Breakeven", "Loss", "No trades"],
            horizontal=True,
        )
        moved_stop = st.radio(
            "Did you move your stop loss?",
            ["No — held discipline", "Yes — to breakeven", "Yes — widened (bad)", "N/A"],
            horizontal=True,
        )
        better_entry = st.radio(
            "Was there a better entry available?",
            ["No — entry was optimal", "Yes — could have waited", "Yes — entered too early", "N/A"],
            horizontal=True,
        )
        emotions = st.multiselect(
            "Emotions during the session:",
            ["Calm & focused", "FOMO", "Revenge trading urge", "Overconfident",
             "Anxious", "Hesitant", "Impatient", "Disciplined", "Frustrated"],
        )
        notes = st.text_area("Lessons learned / notes:", height=120)

        if st.form_submit_button("Submit Review", type="primary"):
            save_journal_entry(trade_id, followed_plan, outcome, moved_stop,
                               better_entry, emotions, notes)
            st.success("Review saved.")
            st.balloons()

    st.divider()
    st.subheader("Past Reviews")
    entries = get_journal_entries()
    if entries:
        st.dataframe(pd.DataFrame(entries), width="stretch")
    else:
        st.info("No reviews yet.")


# ─────────────────────────────────────────────
# Risk Status
# ─────────────────────────────────────────────
def show_risk_status():
    st.header("Risk Management Status")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Max Risk / Trade", f"{config.MAX_RISK_PER_TRADE}%")
    c2.metric("Daily DD Limit", f"{config.MAX_DAILY_DRAWDOWN}%")
    c3.metric("Weekly DD Limit", f"{config.MAX_WEEKLY_DRAWDOWN}%")
    c4.metric("Max Consec. Losses", config.MAX_CONSECUTIVE_LOSSES)

    st.divider()
    risk_log = get_risk_log(200)
    if risk_log:
        df = pd.DataFrame(risk_log).sort_values("logged_at")

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["logged_at"], y=df["daily_dd"],
                                  name="Daily DD %", line=dict(color="#ef5350")))
        fig.add_trace(go.Scatter(x=df["logged_at"], y=df["weekly_dd"],
                                  name="Weekly DD %", line=dict(color="#ffd54f")))
        fig.add_hline(y=config.MAX_DAILY_DRAWDOWN, line_dash="dash",
                      line_color="#ef5350", annotation_text="Daily Limit")
        fig.add_hline(y=config.MAX_WEEKLY_DRAWDOWN, line_dash="dash",
                      line_color="#ffd54f", annotation_text="Weekly Limit")
        fig.update_layout(title="Drawdown History", height=320,
                          margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig, width="stretch")

        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=df["logged_at"], y=df["consecutive_losses"],
                                   fill="tozeroy", name="Consec. Losses",
                                   line=dict(color="#b39ddb")))
        fig2.add_hline(y=config.MAX_CONSECUTIVE_LOSSES, line_dash="dash",
                       line_color="#ef5350", annotation_text="Limit")
        fig2.update_layout(title="Consecutive Loss Tracker", height=240,
                           margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig2, width="stretch")
    else:
        st.info("No risk log data yet.")

    st.divider()
    st.subheader("Constitution Rules")
    rules = [
        f"Max risk per trade: **{config.MAX_RISK_PER_TRADE}%** of balance",
        f"Daily drawdown limit: **{config.MAX_DAILY_DRAWDOWN}%** — alerts suspended if breached",
        f"Weekly drawdown limit: **{config.MAX_WEEKLY_DRAWDOWN}%** — alerts suspended if breached",
        f"Max **{config.MAX_CONSECUTIVE_LOSSES}** consecutive losses before trading is paused",
        "Elite trades: only when score ≥ 12 and daily DD < 50% of limit",
        f"News blackout: **{config.NEWS_BLOCK_MINUTES_BEFORE}m before** / **{config.NEWS_BLOCK_MINUTES_AFTER}m after** HIGH-impact events",
    ]
    for r in rules:
        st.write(f"• {r}")


# ─────────────────────────────────────────────
# Paper Trades
# ─────────────────────────────────────────────
def show_paper_trades():
    st.header("Paper Trading Performance")
    st.caption(
        "Setiap signal membuka posisi virtual 0.01 lot. Hasil SL/TP dicatat "
        "otomatis dan win rate per strategi terus diperbarui."
    )

    recompute_strategy_stats()
    overall = get_paper_overall_stats()

    closed_total = (
        (overall["wins"] or 0) + (overall["losses"] or 0)
        + (overall["breakeven"] or 0) + (overall["expired"] or 0)
    )
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Signals", overall["total"])
    c2.metric("Open", overall["open_trades"])
    c3.metric("Closed", closed_total)
    c4.metric("Win Rate", f"{overall['win_rate']:.1f}%")
    c5.metric("Total R", f"{overall['total_r']:+.2f}")
    c6.metric("PnL", f"{overall['total_pnl']:+.2f}")

    st.divider()
    st.subheader("Win Rate per Strategy")
    stats = get_strategy_stats()
    if stats:
        df_stats = pd.DataFrame(stats)
        display = df_stats[[
            "strategy", "signals", "open_trades", "wins", "losses",
            "breakeven", "expired", "closed_trades", "win_rate",
            "avg_r", "total_r", "profit_factor",
        ]].rename(columns={
            "strategy": "Strategy",
            "signals": "Signals",
            "open_trades": "Open",
            "wins": "Wins",
            "losses": "Losses",
            "breakeven": "BE",
            "expired": "Expired",
            "closed_trades": "Closed",
            "win_rate": "Win %",
            "avg_r": "Avg R",
            "total_r": "Total R",
            "profit_factor": "Profit Factor (R)",
        })
        st.dataframe(display, width="stretch", hide_index=True)

        fig = px.bar(
            df_stats.sort_values("win_rate", ascending=False),
            x="strategy", y="win_rate", text="win_rate",
            title="Win Rate by Strategy (%)",
            color="strategy",
        )
        fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
        fig.update_layout(
            height=320, margin=dict(l=0, r=0, t=40, b=0),
            showlegend=False, yaxis_range=[0, 105],
        )
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Belum ada signal yang tercatat. Jalankan scanner dulu.")

    st.divider()
    tab_open, tab_closed, tab_curve = st.tabs(
        ["Open Positions", "Closed Trades", "Cumulative R"]
    )

    with tab_open:
        open_trades = get_open_paper_trades()
        if open_trades:
            df_open = pd.DataFrame(open_trades)
            cols = ["id", "opened_at", "symbol", "strategy", "direction",
                    "lot", "entry", "sl", "tp1", "tp2"]
            st.dataframe(df_open[cols], width="stretch", hide_index=True)
        else:
            st.info("Tidak ada posisi terbuka.")

    with tab_closed:
        closed = [t for t in get_paper_trades(500) if t["status"] != "open"]
        if not closed:
            st.info("Belum ada posisi yang ditutup.")
        else:
            df_closed = pd.DataFrame(closed)
            f1, f2 = st.columns(2)
            strategies = sorted(df_closed["strategy"].dropna().unique().tolist())
            sel_s = f1.multiselect("Strategy", strategies, default=strategies)
            reasons = sorted(df_closed["exit_reason"].dropna().unique().tolist())
            sel_r = f2.multiselect("Exit reason", reasons, default=reasons)
            view = df_closed[
                df_closed["strategy"].isin(sel_s)
                & df_closed["exit_reason"].isin(sel_r)
            ]
            cols = ["id", "opened_at", "closed_at", "symbol", "strategy",
                    "direction", "lot", "entry", "sl", "tp1", "exit_price",
                    "exit_reason", "r_multiple", "pnl"]
            st.dataframe(view[cols], width="stretch", hide_index=True)

    with tab_curve:
        closed = [t for t in get_paper_trades(500) if t["status"] != "open"]
        if closed:
            df_c = pd.DataFrame(closed)
            df_c["closed_at"] = pd.to_datetime(df_c["closed_at"], errors="coerce")
            df_c = df_c.dropna(subset=["closed_at"]).sort_values("closed_at")
            df_c["cum_r"] = df_c["r_multiple"].fillna(0).cumsum()
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_c["closed_at"], y=df_c["cum_r"],
                fill="tozeroy", name="Cumulative R",
                line=dict(color="#26a69a"),
            ))
            fig.update_layout(
                title="Cumulative R (paper trades)", height=320,
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("Belum ada data.")


if __name__ == "__main__":
    main()
