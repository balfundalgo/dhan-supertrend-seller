from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def _fmt_num(x: Any, digits: int = 2) -> str:
    if x is None:
        return "-"
    try:
        return f"{float(x):.{digits}f}"
    except Exception:
        return "-"


def _fmt_int(x: Any) -> str:
    if x is None:
        return "-"
    try:
        return str(int(x))
    except Exception:
        return "-"


def _fmt_time_from_epoch(epoch_val: Any) -> str:
    if epoch_val is None:
        return "--:--"
    try:
        return datetime.fromtimestamp(int(epoch_val)).strftime("%H:%M")
    except Exception:
        return "--:--"


def _safe_change_pct(open_px: Any, close_px: Any) -> Optional[float]:
    try:
        o = float(open_px)
        c = float(close_px)
        if o == 0:
            return None
        return ((c - o) / o) * 100.0
    except Exception:
        return None


def _fmt_text(x: Any) -> str:
    if x is None:
        return "-"
    s = str(x).strip()
    return s if s else "-"


@dataclass
class DashboardState:
    symbol: str
    timeframe_minutes: int
    variant: int
    st_period: int
    st_multiplier: float

    ws_connected: bool = False
    ws_status_text: str = "Not connected"
    last_ws_error: Optional[str] = None
    ws_start_time: float = field(default_factory=time.time)

    last_ltp: Optional[float] = None
    last_ltt_epoch: Optional[int] = None

    current_candle: Optional[Dict[str, Any]] = None
    recent_closed: List[Dict[str, Any]] = field(default_factory=list)

    st_snapshot: Dict[str, Any] = field(default_factory=dict)
    signal_snapshot: Dict[str, Any] = field(default_factory=dict)
    option_snapshot: Dict[str, Any] = field(default_factory=dict)
    paper_snapshot: Dict[str, Any] = field(default_factory=dict)

    recent_events: List[str] = field(default_factory=list)

    def add_event(self, text: str, limit: int = 10) -> None:
        now_txt = datetime.now().strftime("%H:%M:%S")
        self.recent_events.insert(0, f"[{now_txt}] {text}")
        if len(self.recent_events) > limit:
            self.recent_events = self.recent_events[:limit]

    def update_status(self, msg: str, connected: Optional[bool] = None, error: Optional[str] = None) -> None:
        self.ws_status_text = msg
        if connected is not None:
            self.ws_connected = bool(connected)
            if connected and not self.ws_start_time:
                self.ws_start_time = time.time()
        if error:
            self.last_ws_error = error

    def update_market(
        self,
        ltp: Optional[float],
        ltt_epoch: Optional[int],
        current_candle: Optional[Dict[str, Any]],
        recent_closed: Optional[List[Dict[str, Any]]],
        st_snapshot: Optional[Dict[str, Any]],
        signal_snapshot: Optional[Dict[str, Any]],
        option_snapshot: Optional[Dict[str, Any]] = None,
        paper_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        if ltp is not None:
            try:
                self.last_ltp = float(ltp)
            except Exception:
                pass

        if ltt_epoch is not None:
            try:
                self.last_ltt_epoch = int(ltt_epoch)
            except Exception:
                pass

        self.current_candle = dict(current_candle) if current_candle else None
        self.recent_closed = list(recent_closed) if recent_closed else []
        self.st_snapshot = dict(st_snapshot) if st_snapshot else {}
        self.signal_snapshot = dict(signal_snapshot) if signal_snapshot else {}

        if option_snapshot is not None:
            self.option_snapshot = dict(option_snapshot)
        if paper_snapshot is not None:
            self.paper_snapshot = dict(paper_snapshot)


class DashboardPrinter:
    def __init__(self, state: DashboardState, refresh_seconds: float = 1.0) -> None:
        self.state = state
        self.refresh_seconds = float(refresh_seconds)
        self._running = True

    def stop(self) -> None:
        self._running = False

    def _clear(self) -> None:
        os.system("cls" if os.name == "nt" else "clear")

    def _ws_uptime(self) -> str:
        try:
            secs = max(0, int(time.time() - self.state.ws_start_time))
            return f"{secs}s"
        except Exception:
            return "-"

    def _feed_status(self) -> str:
        if self.state.last_ltt_epoch is None:
            return "STALE"
        try:
            age = int(time.time()) - int(self.state.last_ltt_epoch)
            return "LIVE" if age <= 5 else "STALE"
        except Exception:
            return "STALE"

    def _next_close_text(self) -> str:
        cc = self.state.current_candle
        if not cc:
            return "--"
        try:
            end_epoch = int(cc.get("end_epoch"))
            rem = max(0, end_epoch - int(time.time()))
            return f"{rem}s"
        except Exception:
            return "--"

    def _render_header(self) -> str:
        now_txt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"  Dhan Supertrend Strategy Dashboard  │  {self.state.symbol}  │  "
            f"{self.state.timeframe_minutes}m  │  {now_txt}  │  WS uptime: {self._ws_uptime()}"
        )

    def _render_market(self) -> str:
        cc = self.state.current_candle or {}

        candle_time = _fmt_time_from_epoch(
            cc.get("bucket", cc.get("epoch", cc.get("time", self.state.last_ltt_epoch)))
        )

        return "\n".join([
            "════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════",
            "  Symbol          Time         Open        High         Low       Close    Ticks      Next    Feed            WS",
            "────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────",
            f"  {self.state.symbol:<14}"
            f"{candle_time:>8}    "
            f"{_fmt_num(cc.get('open')):>10}    "
            f"{_fmt_num(cc.get('high')):>10}    "
            f"{_fmt_num(cc.get('low')):>10}    "
            f"{_fmt_num(cc.get('close')):>10}    "
            f"{_fmt_int(cc.get('ticks')):>5}    "
            f"{self._next_close_text():>6}    "
            f"{self._feed_status():>5}     "
            f"{'CONNECTED' if self.state.ws_connected else 'DISCONNECTED'}",
            "────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────",
        ])

    def _render_signal_block(self) -> str:
        st = self.state.st_snapshot or {}
        sig = self.state.signal_snapshot or {}

        indicator_seeded = sig.get("indicator_seeded")
        live_action_armed = sig.get("live_action_armed")
        active = sig.get("active")
        waiting_reentry = sig.get("waiting_reentry")
        signal_candle_high = sig.get("signal_candle_high")
        signal_candle_low = sig.get("signal_candle_low")

        trend = sig.get("trend") or st.get("trend") or "-"
        flip_signal = sig.get("signal") or "-"

        lines = [
            f"  Variant={self.state.variant}  ST(period={self.state.st_period}, mult={self.state.st_multiplier})  "
            f"Trend={trend}  Flip Signal={flip_signal}  ST Value={_fmt_num(st.get('supertrend'))}  ATR={_fmt_num(st.get('atr'))}",
            f"  Indicator Seeded={'YES' if indicator_seeded else 'NO'}  "
            f"Live Armed={'YES' if live_action_armed else 'NO'}  "
            f"Active={active or '-'}  Waiting Re-entry={'YES' if waiting_reentry else 'NO'}  "
            f"Signal Candle High={_fmt_num(signal_candle_high)}  Low={_fmt_num(signal_candle_low)}",
            f"  WS Status: {self.state.ws_status_text}",
        ]

        if self.state.last_ws_error:
            lines.append(f"  Last WS error: {self.state.last_ws_error}")

        return "\n".join(lines)

    def _render_option_block(self) -> str:
        opt = self.state.option_snapshot or {}
        paper = self.state.paper_snapshot or {}

        has_setup = bool(opt.get("has_setup"))
        has_active_position = bool(paper.get("has_active_position"))
        active_position = paper.get("active_position") or {}

        lines = [
            "",
            "  Phase 4 Option Discovery + Auto Rescan",
            "  ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────",
            f"  Expiries  | current={_fmt_text(opt.get('expiry_current'))} | next={_fmt_text(opt.get('expiry_next'))} | far={_fmt_text(opt.get('expiry_far'))}",
        ]

        if has_setup:
            lines.extend([
                f"  Setup     | trend={_fmt_text(opt.get('trend'))} | expiry={_fmt_text(opt.get('expiry_text'))} | "
                f"OTM={_fmt_text(opt.get('otm_step'))} | hedge_steps={_fmt_text(opt.get('hedge_distance_steps'))}",
                f"  Short Leg | {_fmt_text(opt.get('short_symbol'))} | strike={_fmt_num(opt.get('short_strike'))} | premium={_fmt_num(opt.get('short_premium'))}",
                f"  Hedge Leg | {_fmt_text(opt.get('hedge_symbol'))} | strike={_fmt_num(opt.get('hedge_strike'))} | premium={_fmt_num(opt.get('hedge_premium'))}",
                f"  Net Credit= {_fmt_num(opt.get('net_credit'))}",
            ])
        else:
            lines.append(f"  Setup     | No valid option setup yet")

        lines.append(f"  Discovery | {_fmt_text(opt.get('last_reason'))}")

        if has_active_position:
            p = active_position
            pnl_pts = p.get("pnl_points")
            pnl_rs  = p.get("pnl_rupees")
            sl_hit  = p.get("short_sl_hit", False)

            pnl_pts_txt = ("-" if pnl_pts is None else
                           f"{'▲' if float(pnl_pts) >= 0 else '▼'} {abs(float(pnl_pts)):.2f}")
            pnl_rs_txt  = ("-" if pnl_rs is None else
                           f"{'▲' if float(pnl_rs) >= 0 else '▼'} ₹{abs(float(pnl_rs)):.0f}")
            sl_txt = "  ⚠ SHORT SL HIT" if sl_hit else ""

            lines.extend([
                f"  Paper Pos | OPEN | trend={_fmt_text(p.get('trend'))} | entry={_fmt_text(p.get('entry_time'))} | spot@entry={_fmt_num(p.get('entry_spot'))}",
                f"  Entry     | short={_fmt_text(p.get('short_symbol'))} @ {_fmt_num(p.get('short_entry_premium'))} | "
                f"hedge={_fmt_text(p.get('hedge_symbol'))} @ {_fmt_num(p.get('hedge_entry_premium'))} | "
                f"net_credit={_fmt_num(p.get('net_credit_entry'))}",
                f"  Live MTM  | short_ltp={_fmt_num(p.get('short_ltp'))} | hedge_ltp={_fmt_num(p.get('hedge_ltp'))} | "
                f"net_now={_fmt_num(p.get('net_premium_now'))} | "
                f"P&L pts={pnl_pts_txt} | P&L ₹={pnl_rs_txt}{sl_txt}",
                f"  Extremes  | max_profit={_fmt_num(p.get('max_profit_seen'))} pts | "
                f"max_loss={_fmt_num(p.get('max_loss_seen'))} pts | "
                f"sl_price={_fmt_num(p.get('short_sl_price'))} | updated={_fmt_text(p.get('mtm_last_update'))}",
            ])
        else:
            lines.append("  Paper Pos | No active paper position")

        if paper.get("last_event"):
            lines.append(f"  Paper Log | {_fmt_text(paper.get('last_event'))}")

        return "\n".join(lines)

    def _render_closed_candles(self) -> str:
        lines = [
            "",
            f"  Last completed {self.state.timeframe_minutes}m candles  (most recent first)",
            "  Time         Open        High         Low       Close      Chg%    Ticks",
            "  ────────────────────────────────────────────────────────────────────────────────",
        ]

        if not self.state.recent_closed:
            lines.append("  No completed candle yet")
            return "\n".join(lines)

        for c in self.state.recent_closed[:8]:
            t = _fmt_time_from_epoch(c.get("bucket", c.get("epoch", c.get("time"))))
            chg = _safe_change_pct(c.get("open"), c.get("close"))
            chg_txt = "-" if chg is None else f"{'▲' if chg >= 0 else '▼'} {abs(chg):.2f}%"

            lines.append(
                f"  {t:<8}  "
                f"{_fmt_num(c.get('open')):>10}    "
                f"{_fmt_num(c.get('high')):>10}    "
                f"{_fmt_num(c.get('low')):>10}    "
                f"{_fmt_num(c.get('close')):>10}  "
                f"{chg_txt:>8}    "
                f"{_fmt_int(c.get('ticks')):>5}"
            )

        return "\n".join(lines)

    def _render_events(self) -> str:
        lines = [
            "",
            "  Recent strategy events",
            "  ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────",
        ]

        if not self.state.recent_events:
            lines.append("  No events yet")
        else:
            lines.extend(f"  {x}" for x in self.state.recent_events[:10])

        lines.extend([
            "",
            "  Source: Dhan WebSocket ticker feed → local timeframe candle builder → Supertrend → Phase 2/3 signal lifecycle  |  Press Ctrl+C to stop"
        ])
        return "\n".join(lines)

    def render(self) -> str:
        parts = [
            self._render_header(),
            self._render_market(),
            self._render_signal_block(),
            self._render_option_block(),
            self._render_closed_candles(),
            self._render_events(),
        ]
        return "\n".join([p for p in parts if p is not None])

    def run(self) -> None:
        while self._running:
            self._clear()
            print(self.render(), flush=True)
            time.sleep(self.refresh_seconds)