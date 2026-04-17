"""
app.py — Balfund Supertrend Option Seller
GUI application (CustomTkinter) that wraps the full strategy engine.

Tabs:
  1. Token Manager  — credentials + TOTP token generation
  2. Strategy Setup — all parameters (variant, timeframe, ST, SL, rollover, etc.)
  3. Live Dashboard — real-time candles, signals, option setup, paper P&L, event log
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import customtkinter as ctk

# ── Colour palette (Balfund dark theme) ──────────────────────────────────────
DARK_BG   = "#0f1117"
PANEL_BG  = "#1a1d27"
CARD_BG   = "#20232f"
BORDER    = "#2e3247"
ACCENT    = "#e63946"
ACCENT2   = "#4361ee"
GREEN     = "#2dc653"
RED       = "#e63946"
YELLOW    = "#f4a261"
WHITE     = "#f0f0f0"
MUTED     = "#8b8fa8"
FONT_MONO = ("Courier New", 11)
FONT_SM   = ("Segoe UI", 10)
FONT_MD   = ("Segoe UI", 12)
FONT_LG   = ("Segoe UI", 14, "bold")
FONT_XL   = ("Segoe UI", 18, "bold")


# ─────────────────────────────────────────────────────────────────────────────
# Shared strategy runtime bridge
# ─────────────────────────────────────────────────────────────────────────────
class StrategyBridge:
    """Thread-safe bridge between GUI and running strategy engine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: Dict[str, Any] = {}
        self._events: list[str] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        return self._running

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._snapshot)

    def get_events(self) -> list[str]:
        with self._lock:
            return list(self._events)

    def post_event(self, text: str) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._events.insert(0, f"[{now}] {text}")
            self._events = self._events[:200]

    def update_snapshot(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self._snapshot = dict(data)

    def start(self, cfg_kwargs: dict) -> str:
        if self._running:
            return "Already running"
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, args=(cfg_kwargs,), daemon=True
        )
        self._running = True
        self._thread.start()
        return "OK"

    def stop(self) -> None:
        self._stop_event.set()
        self._running = False

    def _run(self, cfg_kwargs: dict) -> None:
        try:
            self._run_strategy(cfg_kwargs)
        except Exception as e:
            self.post_event(f"Strategy crashed: {e}")
        finally:
            self._running = False

    def _run_strategy(self, kwargs: dict) -> None:
        """
        Imports and runs the strategy engine, continuously feeding
        snapshots back to the bridge for the GUI to display.
        """
        # Set env vars from GUI credentials
        os.environ["DHAN_CLIENT_ID"]    = kwargs.get("client_id", "")
        os.environ["DHAN_PIN"]          = kwargs.get("pin", "")
        os.environ["DHAN_TOTP_SECRET"]  = kwargs.get("totp_secret", "")
        os.environ["DHAN_ACCESS_TOKEN"] = kwargs.get("access_token", "")

        # Strategy config env overrides
        os.environ["DEFAULT_TIMEFRAME_MINUTES"] = str(kwargs.get("timeframe", 60))
        os.environ["DEFAULT_VARIANT"]           = str(kwargs.get("variant", 3))
        os.environ["ST_PERIOD"]                 = str(kwargs.get("st_period", 10))
        os.environ["ST_MULTIPLIER"]             = str(kwargs.get("st_multiplier", 3.0))
        os.environ["SL_PERCENT"]                = str(kwargs.get("sl_percent", 30.0))
        os.environ["OPTION_EXPIRY_MODE"]        = str(kwargs.get("expiry_mode", "AUTO"))
        os.environ["OPTION_OTM_STEPS"]          = str(kwargs.get("otm_steps", "3,4,5,6"))
        os.environ["ROLLOVER_VARIANT"]          = str(kwargs.get("rollover_variant", 2))
        os.environ["LOWER_TF_MINUTES"]          = str(kwargs.get("lower_tf_minutes", 15))
        os.environ["GLOBAL_SL_RUPEES"]          = str(kwargs.get("global_sl_rupees", 0))
        os.environ["MIN_SHORT_PREMIUM"]         = str(kwargs.get("min_short_premium", 200))
        os.environ["MAX_SHORT_PREMIUM"]         = str(kwargs.get("max_short_premium", 300))
        os.environ["MIN_HEDGE_PREMIUM"]         = str(kwargs.get("min_hedge_premium", 50))
        os.environ["MAX_HEDGE_PREMIUM"]         = str(kwargs.get("max_hedge_premium", 90))
        os.environ["MIN_NET_CREDIT"]            = str(kwargs.get("min_net_credit", 150))

        from token_helper import ensure_dhan_token
        from candle_builder import TimeframeCandleBuilder
        from config import AppConfig
        from dhan_ws_client import DhanWSClient
        from history_loader import fetch_intraday_1m_history
        from option_chain_engine import OptionChainEngine
        from option_signal_bridge import OptionDiscoveryConfig, OptionSignalBridge
        from option_ws_client import OptionWSClient
        from paper_positions import PaperPositionManager
        from rollover_engine import NSE_HOLIDAYS, should_rollover_v1_now, should_rollover_v2_now, rollover_info
        from signal_engine import SignalEngine
        from state_manager import StateManager
        from supertrend_engine import SupertrendEngine
        from strategy_logger import (
            setup_logger,
            log, log_debug, log_warn, log_error, log_section,
            log_config, log_candle, log_signal_state,
            log_option_snap, log_paper_snap, log_tick,
            log_trade_open, log_trade_close,
            get_log_path, get_trade_log_path
        )
        # Initialise logger for this session
        setup_logger()
        from datetime import timezone, timedelta
        from typing import Optional as Opt

        _IST = timezone(timedelta(hours=5, minutes=30))

        def ist_now():
            return datetime.now(tz=_IST)

        log_section("STRATEGY START")
        log_config(kwargs)
        self.post_event("Authenticating Dhan token...")
        log("Authenticating Dhan token...")
        try:
            client_id, access_token = ensure_dhan_token()
            self.post_event(f"Token OK | client={client_id}")
            log(f"Token OK | client={client_id}")
        except Exception as e:
            self.post_event(f"Token failed: {e}")
            log_error(f"Token failed: {e}")
            self._running = False
            return

        try:
            self.post_event(f"Log file: {get_log_path()}")
            self.post_event(f"Trade log: {get_trade_log_path()}")
            log(f"Activity log: {get_log_path()}")
            log(f"Trade log: {get_trade_log_path()}")
        except Exception:
            pass

        tf   = int(kwargs.get("timeframe", 60))
        var  = int(kwargs.get("variant", 3))
        stp  = int(kwargs.get("st_period", 10))
        stm  = float(kwargs.get("st_multiplier", 3.0))
        slp        = float(kwargs.get("sl_percent", 30.0))
        ltf        = int(kwargs.get("lower_tf_minutes", 15))
        global_sl  = float(kwargs.get("global_sl_rupees", 0.0))  # 0 = disabled
        seg        = "IDX_I"
        sid  = "13"    # NIFTY

        state_manager   = StateManager("state/runtime_state.json", tf, stp, stm)
        candle_builder  = TimeframeCandleBuilder(tf)
        st_engine       = SupertrendEngine(stp, stm)
        signal_engine   = SignalEngine(var, slp, state_manager)

        # ── Variant 5: dual-TF setup ──────────────────────────────────────────
        ltf_candle_builder = None
        ltf_st_engine      = None
        dual_tf_engine_obj = None

        if var == 5:
            from dual_tf_signal_engine import DualTFSignalEngine
            ltf_candle_builder = TimeframeCandleBuilder(ltf)
            ltf_st_engine      = SupertrendEngine(stp, stm)
            dual_tf_engine_obj = DualTFSignalEngine(sl_percent=slp)
            signal_engine.dual_tf_engine = dual_tf_engine_obj

        oce = OptionChainEngine(
            client_id=client_id, access_token=access_token,
            underlying_scrip=13, underlying_seg=seg,
            atm_step=100, strike_step=50,
        )
        bridge_obj = OptionSignalBridge(
            option_chain_engine=oce,
            discovery_cfg=OptionDiscoveryConfig(
                strike_step=50,
                otm_steps=tuple(int(x) for x in str(kwargs.get("otm_steps","3,4,5,6")).split(",")),
                expiry_mode=str(kwargs.get("expiry_mode","AUTO")),
                min_short_premium=float(kwargs.get("min_short_premium",200)),
                max_short_premium=float(kwargs.get("max_short_premium",300)),
                min_hedge_premium=float(kwargs.get("min_hedge_premium",50)),
                max_hedge_premium=float(kwargs.get("max_hedge_premium",90)),
                min_net_credit=float(kwargs.get("min_net_credit",150)),
            ),
        )
        paper_mgr = PaperPositionManager()

        option_ws = OptionWSClient(
            client_id=client_id, access_token=access_token,
            on_tick=lambda sid_, ltp_, ltt_: _on_opt_tick(sid_, ltp_, ltt_),
            on_status=lambda m: self.post_event(f"OptionWS: {m}"),
        )

        v1_exit_today    = False
        _930_done        = None
        _rollover_done   = None
        last_flip        = None
        last_active      = None
        boot_done        = False
        last_disc_epoch  = None
        _global_sl_hit   = False    # True = global SL triggered today → no new trades
        _global_sl_date  = None     # date on which it triggered (reset next day)

        def _sync_ws():
            option_ws.set_instruments(paper_mgr.active_option_watchlist())

        def _day_pnl_rupees() -> float:
            """Total realized + unrealized P&L for today in rupees."""
            try:
                snap = paper_mgr.snapshot()
                pos  = snap.get("active_position") or {}
                real = float(pos.get("pnl_rupees") or 0.0)
                hist = snap.get("history") or []
                # sum realized from today's closed trades
                from datetime import datetime as _dt
                today_str = _dt.now().strftime("%Y-%m-%d")
                closed_today = sum(
                    float(h.get("pnl_estimate") or 0.0) * float(snap.get("lot_size", 75))
                    for h in hist
                    if str(h.get("exit_time", "")).startswith(today_str)
                )
                return real + closed_today
            except Exception:
                return 0.0

        def _check_global_sl() -> bool:
            """
            Returns True if global SL is active (already hit today).
            If SL just breached, closes position, logs, fires dashboard alert.
            """
            nonlocal _global_sl_hit, _global_sl_date
            today = ist_now().date()

            # reset flag on new day
            if _global_sl_date != today:
                _global_sl_hit  = False
                _global_sl_date = today

            if _global_sl_hit:
                return True  # already hit today

            if global_sl <= 0:
                return False  # disabled

            pnl = _day_pnl_rupees()
            if pnl <= -abs(global_sl):
                _global_sl_hit = True
                _global_sl_date = today
                msg = (
                    f"🛑 GLOBAL SL HIT | day P&L = ₹{pnl:.0f} "
                    f"≤ -₹{global_sl:.0f} | closing position & stopping trades for today"
                )
                self.post_event(msg)
                log(f"GLOBAL SL: {msg}")
                if paper_mgr.has_active():
                    _pre_close_snap = paper_mgr.snapshot()
                    close_msg = paper_mgr.close_active_position("Global daily SL hit")
                    self.post_event(close_msg)
                    log(f"GLOBAL SL CLOSE: {close_msg}")
                    log_trade_close(_pre_close_snap, exit_reason="Global daily SL hit")
                    _sync_ws()
                return True

            return False

        def _on_opt_tick(security_id, ltp, ltt):
            nonlocal v1_exit_today, _global_sl_hit, _global_sl_date
            sl_ev = paper_mgr.update_live_quote(security_id, ltp, ltt)
            if sl_ev:
                self.post_event(sl_ev)
                log(f"SL ALERT: {sl_ev}")
                if var in (1, 4) and paper_mgr.has_active():
                    pos = paper_mgr.snapshot().get("active_position") or {}
                    if pos.get("short_sl_hit"):
                        _pre_close_snap = paper_mgr.snapshot()
                        msg = paper_mgr.close_active_position(
                            f"Variant {var}: SL breached on live tick"
                        )
                        self.post_event(msg)
                        log(f"SL CLOSE: {msg}")
                        log_paper_snap(paper_mgr.snapshot())
                        log_trade_close(_pre_close_snap, exit_reason=f"V{var}: SL breached on live tick")
                        _sync_ws()
                        if var == 1:
                            v1_exit_today = True
            # Also check global SL after every MTM update
            _check_global_sl()
            _push_snapshot()

        def _push_snapshot():
            snap = candle_builder.snapshot()
            self.update_snapshot({
                "ltp": snap.get("last_ltp"),
                "ltt_epoch": snap.get("last_ltt_epoch"),
                "current_candle": snap.get("current"),
                "recent_closed": snap.get("history", [])[:8],
                "st": st_engine.snapshot(),
                "signal": signal_engine.snapshot(),
                "option": bridge_obj.snapshot(),
                "paper": paper_mgr.snapshot(),
            })

        def _candle_epoch(c):
            for k in ("bucket","epoch","start_epoch","ts","timestamp","time"):
                if c.get(k) is not None:
                    return int(c[k])
            return None

        def _actionable_trend():
            s = signal_engine.snapshot()
            if s.get("active") == "SHORT_PUT":  return "BUY"
            if s.get("active") == "SHORT_CALL": return "SELL"
            t = str(s.get("trend") or "").upper()
            if t in ("BUY", "SELL"):
                return t   # trend alone is enough for entry discovery
            return None

        def _discover(*, spot, trend, prefix, epoch=None):
            nonlocal last_disc_epoch, last_active
            log_section(f"OPTION DISCOVERY — {prefix.upper()}")
            log(f"Attempting discovery | spot={spot:.2f} | trend={trend} | prefix={prefix}")
            log_signal_state(signal_engine.snapshot())
            setup, reason = bridge_obj.discover_setup(spot_price=spot, trend=trend)
            self.post_event(f"{prefix} | {reason}")
            log(f"Discovery result: {reason}")
            if setup is not None:
                msg = paper_mgr.open_position(setup, spot_price=spot, sl_percent=slp)
                self.post_event(msg)
                log(f"PAPER OPEN: {msg}")
                # Only sync state and log trade if position actually opened
                # (paper_mgr returns "skipped" message if already active)
                if paper_mgr.has_active():
                    pos = paper_mgr.snapshot().get("active_position") or {}
                    pos_trend = pos.get("trend", "")
                    # Sync last_active so exit gate works on next candle close
                    if pos_trend == "BUY":
                        last_active = "SHORT_PUT"
                    elif pos_trend == "SELL":
                        last_active = "SHORT_CALL"
                    _sync_ws()
                    log_paper_snap(paper_mgr.snapshot())
                    log_trade_open(paper_mgr.snapshot())
            else:
                log_warn(f"No setup found — reason: {reason}")
                log_option_snap(bridge_obj.snapshot())
            if epoch is not None:
                last_disc_epoch = int(epoch)
            _push_snapshot()

        def _after_1015():
            n = ist_now()
            return n.hour > 10 or (n.hour == 10 and n.minute >= 15)

        def _market_is_open() -> bool:
            """Returns True only after 09:15 IST — no trades before market opens."""
            n = ist_now()
            return (n.hour > 9) or (n.hour == 9 and n.minute >= 15)

        # ── bootstrap ─────────────────────────────────────────────────────
        log_section("BOOTSTRAP — HISTORY")
        self.post_event("Loading history...")
        candles_1m, hmsg = fetch_intraday_1m_history(
            client_id=client_id, access_token=access_token,
            security_id=sid, exchange_segment=seg,
            lookback_days=30, limit=5000, instrument="INDEX",
        )
        self.post_event(hmsg)
        log(hmsg)
        if candles_1m:
            n_tf = candle_builder.seed_from_1m_history(candles_1m)
            self.post_event(f"Seeded {n_tf} x {tf}m candles")
            log(f"Seeded {n_tf} x {tf}m candles from {len(candles_1m)} x 1m bars")
            hist_list = list(reversed(candle_builder.snapshot().get("history",[])))
            for c in hist_list:
                r = st_engine.update(c)
                signal_engine.process_historical_candle(c, r)
            state_manager.mark_indicator_seeded()

            # ── Reconcile startup state ────────────────────────────────────
            # If no flip found in history window, seed active state from
            # current trend so the system can enter trades immediately.
            reconcile_msg = signal_engine.reconcile_startup_state(
                bootstrap_current_trend=True
            )
            if reconcile_msg:
                self.post_event(f"Startup reconcile: {reconcile_msg}")
                log(f"RECONCILE: {reconcile_msg}")

            # Log final state after warmup + reconcile
            sig_snap = signal_engine.snapshot()
            log(f"Post-warmup | trend={sig_snap.get('trend')} | signal={sig_snap.get('signal')} | "
                f"active={sig_snap.get('active')} | sig_hi={sig_snap.get('signal_candle_high')} | "
                f"sig_lo={sig_snap.get('signal_candle_low')}")

            # Variant 5: seed LTF as well
            if var == 5 and ltf_candle_builder is not None and dual_tf_engine_obj is not None:
                n_ltf = ltf_candle_builder.seed_from_1m_history(candles_1m)
                self.post_event(f"V5 LTF: seeded {n_ltf} x {ltf}m candles")
                log(f"V5 LTF seeded {n_ltf} x {ltf}m candles")
                for c in reversed(list(ltf_candle_builder.snapshot().get("history", []))):
                    r = ltf_st_engine.update(c)
                    dual_tf_engine_obj.process_ltf_historical(c, r)
                for c in reversed(list(candle_builder.snapshot().get("history", []))):
                    r2 = st_engine.update(c)
                    dual_tf_engine_obj.process_htf_historical(c, r2)
                dual_tf_engine_obj.mark_htf_seeded()
                dual_tf_engine_obj.mark_ltf_seeded()
                v5snap = dual_tf_engine_obj.snapshot()
                self.post_event(f"V5 dual-TF warmup complete | HTF={tf}m LTF={ltf}m")
                log(f"V5 post-warmup | HTF trend={v5snap.get('htf_trend')} | LTF trend={v5snap.get('ltf_trend')}")

            self.post_event("Warmup complete")
            log_section("BOOTSTRAP — OPTION CHAIN")

        self.post_event("Fetching expiry list...")
        log("Fetching expiry list from Dhan API...")
        for attempt in range(1, 6):
            try:
                emsg = bridge_obj.refresh_master()
                expiry_list = bridge_obj.last_expiry_info.get("all") or []
                if expiry_list:
                    self.post_event(emsg)
                    log(emsg)
                    break
                else:
                    wait = 2 ** attempt
                    self.post_event(f"Expiry list empty, retry {attempt}/5 in {wait}s...")
                    log_warn(f"Expiry list empty, retry {attempt}/5 in {wait}s...")
                    time.sleep(wait)
            except Exception as e:
                wait = 2 ** attempt
                self.post_event(f"Expiry fetch error: {e} — retry {attempt}/5 in {wait}s...")
                log_error(f"Expiry fetch error: {e} — retry {attempt}/5 in {wait}s...")
                time.sleep(wait)
        else:
            msg = "⚠ Expiry list unavailable after 5 attempts — discovery will retry on each candle close"
            self.post_event(msg)
            log_warn(msg)

        expiry_list = bridge_obj.last_expiry_info.get("all") or []
        if expiry_list:
            rinfo = rollover_info(expiry_list)
            self.post_event(rinfo)
            log(rinfo)
        log_option_snap(bridge_obj.snapshot())

        _push_snapshot()

        # ── tick handler ──────────────────────────────────────────────────
        def on_tick(price, ltt_epoch):
            nonlocal v1_exit_today, _930_done, _rollover_done, last_flip, last_active, boot_done
            nonlocal _global_sl_hit, _global_sl_date
            if self._stop_event.is_set():
                return

            log_tick(price, ltt_epoch, interval=100)

            closed = candle_builder.on_tick(price, ltt_epoch)

            # Variant 5: also route tick to LTF candle builder
            ltf_closed = []
            if var == 5 and ltf_candle_builder is not None:
                ltf_closed = ltf_candle_builder.on_tick(price, ltt_epoch)

            _push_snapshot()

            today = ist_now().date()
            n = ist_now()

            # Reset per-day flags at start of new day
            if _global_sl_date != today:
                _global_sl_hit  = False
                _global_sl_date = today

            # Check global SL on every tick (catches intraday breach)
            _check_global_sl()

            # 9:30 AM Variant 1 check
            if var == 1 and n.hour == 9 and n.minute == 30 and _930_done != today:
                _930_done = today
                v1_exit_today = False
                log("9:30 AM SL check triggered (Variant 1)")
                if paper_mgr.has_active():
                    pos = paper_mgr.snapshot().get("active_position") or {}
                    if pos.get("short_sl_hit"):
                        _pre_close_snap = paper_mgr.snapshot()
                        msg = paper_mgr.close_active_position("V1: 9:30 gap-up SL check")
                        self.post_event(msg)
                        log(f"V1 9:30 SL close: {msg}")
                        log_trade_close(_pre_close_snap, exit_reason="V1: 9:30 gap-up SL check")
                        _sync_ws()
                        v1_exit_today = True

            # 3 PM rollover check
            if n.hour == 15 and _rollover_done != today:
                rv = int(kwargs.get("rollover_variant", 2))
                el = bridge_obj.last_expiry_info.get("all") or []
                triggered = False
                if rv == 1 and el:
                    triggered = should_rollover_v1_now(el)
                elif rv == 2 and el:
                    triggered = should_rollover_v2_now(el, NSE_HOLIDAYS)
                if triggered:
                    _rollover_done = today
                    self.post_event(f"Rollover V{rv} triggered @ 3 PM IST")
                    log(f"ROLLOVER V{rv} triggered")
                    if paper_mgr.has_active():
                        _pre_close_snap = paper_mgr.snapshot()
                        msg = paper_mgr.close_active_position("Rollover close")
                        self.post_event(msg)
                        log(f"Rollover close: {msg}")
                        log_trade_close(_pre_close_snap, exit_reason=f"Rollover V{rv}")
                        _sync_ws()
                    trend = _actionable_trend()
                    if trend and price:
                        _discover(spot=float(price), trend=trend, prefix=f"Rollover V{rv} re-entry")

            # bootstrap discovery
            if not boot_done:
                trend = _actionable_trend()
                log(f"Bootstrap discovery check | actionable_trend={trend} | has_active={paper_mgr.has_active()} | market_open={_market_is_open()}")
                log_signal_state(signal_engine.snapshot())
                if trend and not paper_mgr.has_active() and not _check_global_sl() and _market_is_open():
                    _discover(spot=float(price), trend=trend, prefix="Bootstrap")
                    last_active = signal_engine.snapshot().get("active")
                elif not trend:
                    log_warn("Bootstrap: no actionable trend yet — waiting for signal")
                elif not _market_is_open():
                    log_warn("Bootstrap: market not open yet (before 09:15) — will retry on first candle close")
                boot_done = True

            # ── HTF candles ───────────────────────────────────────────────────
            for candle in closed:
                r = st_engine.update(candle)
                log_candle(f"HTF CANDLE CLOSE ({tf}m)", candle, r)

                if var == 5 and dual_tf_engine_obj is not None:
                    htf_evs = dual_tf_engine_obj.process_htf_live(candle, r)
                    for ev in htf_evs:
                        self.post_event(f"[HTF] {ev}")
                        log(f"[HTF] {ev}")
                    signal_engine.current_trend  = dual_tf_engine_obj.htf_trend
                    signal_engine.current_flip_signal = dual_tf_engine_obj.htf_signal
                    signal_engine.current_st_value = dual_tf_engine_obj.htf_st
                    signal_engine.current_atr    = dual_tf_engine_obj.htf_atr
                else:
                    events = signal_engine.process_live_closed_candle(candle, r)
                    for ev in events:
                        self.post_event(ev)
                        log(f"SIGNAL EVENT: {ev}")

                    sig = signal_engine.snapshot()
                    log_signal_state(sig)
                    cur_active = sig.get("active")

                    if last_active in ("SHORT_PUT","SHORT_CALL") and cur_active is None:
                        if paper_mgr.has_active():
                            reason = sig.get("last_event") or "Signal exit"
                            # Issue 5 fix: "Live action armed" is NOT an exit signal —
                            # it just means the indicator finished warming up. Skip close.
                            if "armed" in reason.lower():
                                log_debug(f"Close gate skipped — reason is indicator armed, not an exit: {reason}")
                            else:
                                _pre_close_snap = paper_mgr.snapshot()
                                msg = paper_mgr.close_active_position(reason)
                                self.post_event(msg)
                                log(f"PAPER CLOSE: {msg}")
                                log_trade_close(_pre_close_snap, exit_reason=reason)
                                _sync_ws()
                                if var == 1:
                                    v1_exit_today = True
                    last_active = cur_active

                    cc  = float(candle["close"])
                    ep  = _candle_epoch(candle)
                    flip = sig.get("signal")
                    if flip in ("BUY","SELL") and flip != last_flip:
                        log(f"FLIP SIGNAL detected: {flip} | v1_gate={var==1 and v1_exit_today and not _after_1015()} | market_open={_market_is_open()}")
                        if not (var == 1 and v1_exit_today and not _after_1015()):
                            if not _check_global_sl() and _market_is_open():
                                # ── Close opposite position before opening new one ──
                                if paper_mgr.has_active():
                                    pos = paper_mgr.snapshot().get("active_position") or {}
                                    pos_trend = pos.get("trend", "")
                                    opposite = (flip == "BUY" and pos_trend == "SELL") or \
                                               (flip == "SELL" and pos_trend == "BUY")
                                    if opposite:
                                        flip_close_reason = sig.get("last_event") or f"Flip to {flip}"
                                        _pre_close_snap = paper_mgr.snapshot()
                                        close_msg = paper_mgr.close_active_position(flip_close_reason)
                                        self.post_event(close_msg)
                                        log(f"FLIP CLOSE (opposite side): {close_msg}")
                                        log_trade_close(_pre_close_snap, exit_reason=flip_close_reason)
                                        _sync_ws()
                                        last_active = None
                                        if var == 1:
                                            v1_exit_today = True
                                _discover(spot=cc, trend=flip, prefix="Flip discovery", epoch=ep)
                            elif not _market_is_open():
                                log_warn("Flip discovery blocked — before 09:15 market open")
                        else:
                            log_warn(f"Flip discovery blocked — V1 exit today and before 10:15 AM")
                        last_flip = flip
                    elif flip in (None,"","-"):
                        last_flip = None

                    # Issue 7 fix: Flat rescan removed.
                    # Per strategy doc, entries happen ONLY on a fresh Supertrend flip signal candle.
                    # Rescanning every candle when flat causes spurious entries not in the strategy.
                    if sig.get("waiting_reentry"):
                        log_debug(f"Waiting re-entry | trend={sig.get('trend')} | sig_hi={sig.get('signal_candle_high')} | close={candle.get('close')}")

                _push_snapshot()

            # ── LTF candles (Variant 5 only) ──────────────────────────────────
            if var == 5 and dual_tf_engine_obj is not None:
                for candle in ltf_closed:
                    ltf_r = ltf_st_engine.update(candle)
                    log_candle(f"LTF CANDLE CLOSE ({ltf}m)", candle, ltf_r)
                    ltf_evs = dual_tf_engine_obj.process_ltf_live(candle, ltf_r)
                    for ev in ltf_evs:
                        self.post_event(f"[LTF] {ev}")
                        log(f"[LTF] {ev}")

                    sig = signal_engine.snapshot()
                    log_signal_state(sig)
                    cur_active = sig.get("active")
                    if last_active in ("SHORT_PUT","SHORT_CALL") and cur_active is None:
                        if paper_mgr.has_active():
                            reason = sig.get("last_event") or "LTF exit"
                            _pre_close_snap = paper_mgr.snapshot()
                            msg = paper_mgr.close_active_position(reason)
                            self.post_event(msg)
                            log(f"PAPER CLOSE (LTF): {msg}")
                            log_trade_close(_pre_close_snap, exit_reason=reason)
                            _sync_ws()
                    last_active = cur_active

                    cc = float(candle["close"])
                    ep = _candle_epoch(candle)
                    # Flat rescan removed per strategy doc — entries on flip signal only

                    _push_snapshot()

        def on_status(msg):
            self.post_event(msg)
            log(f"WS STATUS: {msg}")

        ws = DhanWSClient(
            client_id=client_id, access_token=access_token,
            exchange_segment=seg, security_id=sid,
            on_tick=on_tick, on_status=on_status,
        )

        option_ws.start()
        ws.start()
        v5_info = f" | LTF={ltf}m" if var == 5 else ""
        self.post_event(
            f"Strategy started | variant={var}{v5_info} | HTF={tf}m | ST=({stp},{stm}) | SL={slp}%"
        )
        self.post_event("WebSocket started — strategy live")

        while not self._stop_event.is_set():
            time.sleep(0.2)

        ws.stop()
        option_ws.stop()
        self.post_event("Strategy stopped")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _card(parent, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=10, **kw)


def _label(parent, text, font=FONT_SM, color=WHITE, **kw) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text=text, font=font, text_color=color, **kw)


def _entry(parent, placeholder="", show=None, width=260) -> ctk.CTkEntry:
    kw = dict(
        placeholder_text=placeholder,
        fg_color=PANEL_BG, border_color=BORDER,
        text_color=WHITE, placeholder_text_color=MUTED,
        width=width,
    )
    if show:
        kw["show"] = show
    return ctk.CTkEntry(parent, **kw)


def _fmt(x, digits=2):
    if x is None: return "-"
    try: return f"{float(x):.{digits}f}"
    except: return "-"


def _fmt_epoch(e):
    if e is None: return "--:--"
    try: return datetime.fromtimestamp(int(e)).strftime("%H:%M")
    except: return "--:--"


# ─────────────────────────────────────────────────────────────────────────────
# Tab 1 — Token Manager
# ─────────────────────────────────────────────────────────────────────────────
class TokenTab(ctk.CTkFrame):
    def __init__(self, parent, on_token_saved=None):
        super().__init__(parent, fg_color="transparent")
        self._on_token_saved = on_token_saved
        self._build()
        self._auto_load_credentials()

    def _auto_load_credentials(self) -> None:
        """Auto-fill credentials from .env on startup if available."""
        try:
            from dotenv import load_dotenv
            from dhan_token_manager import ENV_FILE
            load_dotenv(ENV_FILE, override=False)
            cid  = os.getenv("DHAN_CLIENT_ID","").strip()
            pin  = os.getenv("DHAN_PIN","").strip()
            totp = os.getenv("DHAN_TOTP_SECRET","").strip()
            if cid:
                self._entries["Client ID"].delete(0,"end")
                self._entries["Client ID"].insert(0, cid)
            if pin:
                self._entries["PIN"].delete(0,"end")
                self._entries["PIN"].insert(0, pin)
            if totp:
                self._entries["TOTP Secret"].delete(0,"end")
                self._entries["TOTP Secret"].insert(0, totp)
            if cid:
                self._status.configure(
                    text="✅ Credentials auto-loaded from .env", text_color=GREEN
                )
        except Exception:
            pass

    def _build(self):
        wrap = _card(self)
        wrap.pack(fill="both", expand=True, padx=24, pady=24)

        _label(wrap, "Dhan API Credentials", font=FONT_LG).pack(pady=(18, 4))
        _label(wrap, "Enter your credentials. Token is generated automatically via TOTP.",
               color=MUTED).pack(pady=(0, 16))

        grid = ctk.CTkFrame(wrap, fg_color="transparent")
        grid.pack(fill="x", padx=32)
        grid.columnconfigure(1, weight=1)

        fields = [
            ("Client ID",    "10-digit Dhan Client ID",    False),
            ("PIN",          "4-digit trading PIN",        True),
            ("TOTP Secret",  "TOTP secret from web.dhan.co",True),
        ]

        self._entries: dict[str, ctk.CTkEntry] = {}
        for i, (label, ph, hide) in enumerate(fields):
            _label(grid, label, color=MUTED).grid(row=i, column=0, sticky="w", pady=8, padx=(0,16))
            e = _entry(grid, placeholder=ph, show="•" if hide else None, width=320)
            e.grid(row=i, column=1, sticky="ew", pady=8)
            self._entries[label] = e

        self._status = _label(wrap, "", color=MUTED)
        self._status.pack(pady=(16, 4))

        self._token_box = ctk.CTkTextbox(
            wrap, height=60, fg_color=PANEL_BG, border_color=BORDER,
            text_color=GREEN, font=FONT_MONO, wrap="word",
        )
        self._token_box.pack(fill="x", padx=32, pady=(4, 16))
        self._token_box.insert("0.0", "Token will appear here after generation...")
        self._token_box.configure(state="disabled")

        btn_row = ctk.CTkFrame(wrap, fg_color="transparent")
        btn_row.pack(pady=(0, 20))

        ctk.CTkButton(
            btn_row, text="  Generate Token", command=self._generate,
            fg_color=ACCENT2, hover_color="#3451d1", width=180,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            btn_row, text="  Load from .env", command=self._load_env,
            fg_color=PANEL_BG, hover_color=BORDER, border_color=BORDER,
            border_width=1, width=160,
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            btn_row, text="  Clear", command=self._clear,
            fg_color=PANEL_BG, hover_color=BORDER, border_color=BORDER,
            border_width=1, width=100,
        ).pack(side="left", padx=8)

        # ── Shared token section (dhan-token-generator.exe) ────────────────
        sep = ctk.CTkFrame(wrap, fg_color=BORDER, height=1)
        sep.pack(fill="x", padx=32, pady=(8, 12))

        _label(wrap, "🔗  dhan-token-generator  (shared token source)",
               font=FONT_MD, color=MUTED).pack()

        shared_row = ctk.CTkFrame(wrap, fg_color="transparent")
        shared_row.pack(pady=(6, 4))

        self._shared_status = _label(
            shared_row, "Checking...", color=MUTED, font=FONT_SM
        )
        self._shared_status.pack(side="left", padx=(0, 16))

        ctk.CTkButton(
            shared_row, text="🔄  Load from Token Generator",
            command=self._load_from_shared,
            fg_color=PANEL_BG, hover_color=BORDER, border_color=BORDER,
            border_width=1, width=220,
        ).pack(side="left")

        _label(wrap, "Path: C:\\balfund_shared\\dhan_token.json",
               color=MUTED, font=("Segoe UI", 9)).pack(pady=(2, 16))

        # auto-refresh shared token status every 3 seconds
        self._refresh_shared_status()

    def _generate(self):
        client_id   = self._entries["Client ID"].get().strip()
        pin         = self._entries["PIN"].get().strip()
        totp_secret = self._entries["TOTP Secret"].get().strip()

        if not all([client_id, pin, totp_secret]):
            self._status.configure(text="⚠ Please fill all fields", text_color=YELLOW)
            return

        self._status.configure(text="Generating token...", text_color=MUTED)
        self.update()

        def _run():
            try:
                from dhan_token_manager import generate_token_via_totp, save_token_to_env, _save_env_key
                result = generate_token_via_totp(client_id, pin, totp_secret)
                if result.get("success"):
                    token = result["access_token"]
                    save_token_to_env(token, result.get("expiry", ""))
                    # Also persist credentials so they auto-load next time
                    try:
                        _save_env_key("DHAN_CLIENT_ID", client_id)
                        _save_env_key("DHAN_PIN", pin)
                        _save_env_key("DHAN_TOTP_SECRET", totp_secret)
                    except Exception:
                        pass
                    os.environ["DHAN_ACCESS_TOKEN"] = token
                    self._show_token(token, f"✅ Token generated | Expires: {result.get('expiry','')[:19]}")
                    if self._on_token_saved:
                        self._on_token_saved(client_id, token)
                else:
                    self._status.configure(text=f"❌ {result.get('error','Failed')}", text_color=RED)
            except Exception as e:
                self._status.configure(text=f"❌ {e}", text_color=RED)

        threading.Thread(target=_run, daemon=True).start()

    def _load_env(self):
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(".env"))
            cid   = os.getenv("DHAN_CLIENT_ID","")
            tok   = os.getenv("DHAN_ACCESS_TOKEN","")
            pin   = os.getenv("DHAN_PIN","")
            totp  = os.getenv("DHAN_TOTP_SECRET","")
            if cid:
                self._entries["Client ID"].delete(0,"end"); self._entries["Client ID"].insert(0, cid)
            if pin:
                self._entries["PIN"].delete(0,"end"); self._entries["PIN"].insert(0, pin)
            if totp:
                self._entries["TOTP Secret"].delete(0,"end"); self._entries["TOTP Secret"].insert(0, totp)
            if tok:
                self._show_token(tok, "✅ Loaded from .env")
                if self._on_token_saved:
                    self._on_token_saved(cid, tok)
            else:
                self._status.configure(text="Credentials loaded. No token found — generate one.", text_color=YELLOW)
        except Exception as e:
            self._status.configure(text=f"❌ {e}", text_color=RED)

    def _clear(self):
        for e in self._entries.values():
            e.delete(0, "end")
        self._token_box.configure(state="normal")
        self._token_box.delete("0.0", "end")
        self._token_box.insert("0.0", "Token will appear here after generation...")
        self._token_box.configure(state="disabled")
        self._status.configure(text="", text_color=MUTED)

    def _show_token(self, token: str, msg: str):
        self._token_box.configure(state="normal")
        self._token_box.delete("0.0", "end")
        self._token_box.insert("0.0", token)
        self._token_box.configure(state="disabled")
        self._status.configure(text=msg, text_color=GREEN)

    def _load_from_shared(self):
        try:
            from dhan_token_manager import read_shared_token, SHARED_TOKEN_FILE
            shared = read_shared_token()
            if not shared.get("access_token"):
                self._status.configure(
                    text=f"⚠ Shared file not found at {SHARED_TOKEN_FILE}", text_color=YELLOW
                )
                return
            cid = shared["client_id"]
            tok = shared["access_token"]
            # Fill client ID field
            self._entries["Client ID"].delete(0, "end")
            self._entries["Client ID"].insert(0, cid)
            # Show token
            self._show_token(tok, "✅ Loaded from dhan-token-generator shared file")
            os.environ["DHAN_ACCESS_TOKEN"] = tok
            os.environ["DHAN_CLIENT_ID"] = cid
            if self._on_token_saved:
                self._on_token_saved(cid, tok)
        except Exception as e:
            self._status.configure(text=f"❌ {e}", text_color=RED)

    def _refresh_shared_status(self):
        try:
            from dhan_token_manager import read_shared_token, SHARED_TOKEN_FILE
            shared = read_shared_token()
            if shared.get("access_token"):
                cid = shared.get("client_id", "?")
                self._shared_status.configure(
                    text=f"✅  Token found  |  client={cid}",
                    text_color=GREEN,
                )
            elif SHARED_TOKEN_FILE.exists():
                self._shared_status.configure(
                    text="⚠  File exists but token is empty or invalid",
                    text_color=YELLOW,
                )
            else:
                self._shared_status.configure(
                    text="●  Not found — run dhan-token-generator.exe first",
                    text_color=MUTED,
                )
        except Exception:
            self._shared_status.configure(text="●  Unable to check", text_color=MUTED)

        # schedule next refresh in 3 seconds if widget still exists
        try:
            self.after(3000, self._refresh_shared_status)
        except Exception:
            pass

    def get_credentials(self) -> dict:
        return {
            "client_id":   self._entries["Client ID"].get().strip(),
            "pin":         self._entries["PIN"].get().strip(),
            "totp_secret": self._entries["TOTP Secret"].get().strip(),
            "access_token": os.environ.get("DHAN_ACCESS_TOKEN",""),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — Strategy Setup
# ─────────────────────────────────────────────────────────────────────────────
class SetupTab(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self._build()
        self._auto_load()   # load saved params on startup

    # ── params file path (next to exe / script) ───────────────────────────────
    @staticmethod
    def _params_path():
        import sys, json
        from pathlib import Path
        base = Path(sys.executable).parent if getattr(sys, 'frozen', False) \
               else Path(__file__).resolve().parent
        return base / "saved_params.json"

    def _build(self):
        canvas = ctk.CTkScrollableFrame(self, fg_color="transparent")
        canvas.pack(fill="both", expand=True, padx=8, pady=8)

        _label(canvas, "Strategy Configuration", font=FONT_LG).pack(pady=(12,2))
        _label(canvas, "All parameters. Changes take effect on next Start.", color=MUTED).pack(pady=(0,4))

        # ── Save / Load buttons ───────────────────────────────────────────────
        btn_row = ctk.CTkFrame(canvas, fg_color="transparent")
        btn_row.pack(pady=(0, 12))

        ctk.CTkButton(
            btn_row, text="💾  Save Parameters", command=self._save_params,
            fg_color="#14532d", hover_color=GREEN, width=180, height=30,
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            btn_row, text="📂  Load Saved", command=self._load_params,
            fg_color=PANEL_BG, hover_color=BORDER, border_color=BORDER,
            border_width=1, width=140, height=30,
        ).pack(side="left", padx=6)

        self._save_status = _label(btn_row, "", color=MUTED, font=FONT_SM)
        self._save_status.pack(side="left", padx=10)

        # ── two column layout ──────────────────────────────────────────────
        cols = ctk.CTkFrame(canvas, fg_color="transparent")
        cols.pack(fill="x", padx=16)
        cols.columnconfigure((0,1), weight=1, uniform="col")

        left  = _card(cols); left.grid(row=0, column=0, sticky="nsew", padx=(0,8), pady=4)
        right = _card(cols); right.grid(row=0, column=1, sticky="nsew", padx=(8,0), pady=4)

        # ── LEFT: core signal params ───────────────────────────────────────
        _label(left, "Signal & Indicator", font=FONT_MD).pack(pady=(14,8))

        self.var_timeframe    = self._combo(left, "Timeframe (minutes)",
                                            ["1","5","30","45","60","120"], default="60")
        self.var_variant      = self._combo(left, "Strategy Variant",
                                            ["1 — SL% + ST change",
                                             "2 — ST change only",
                                             "3 — Candle breach + ST change",
                                             "4 — Candle breach + SL%",
                                             "5 — Dual TF Trailing (HTF entry + LTF exit)"],
                                            default="3 — Candle breach + ST change")
        self.var_st_period    = self._entry_field(left, "ST Period (integer)", default="10")
        self.var_st_mult      = self._entry_field(left, "ST Multiplier (float)", default="3.0")
        self.var_sl_pct       = self._slider(left, "SL % on Short Premium",
                                             from_=25, to=40, default=30)

        # Lower TF row — only meaningful for Variant 5
        ltf_row = ctk.CTkFrame(left, fg_color="transparent")
        ltf_row.pack(fill="x", padx=16, pady=4)
        ltf_row.columnconfigure(1, weight=1)
        _label(ltf_row, "Lower TF / mins  (Variant 5)", color=MUTED).grid(row=0, column=0, sticky="w")
        self.var_lower_tf = ctk.CTkComboBox(
            ltf_row,
            values=["1","5","10","15","30","45","60","120"],
            width=100,
            fg_color=PANEL_BG, border_color=BORDER,
            button_color=ACCENT2, button_hover_color="#3451d1",
            text_color=WHITE, dropdown_fg_color=PANEL_BG,
        )
        self.var_lower_tf.set("15")
        self.var_lower_tf.grid(row=0, column=1, sticky="e", pady=2)

        _label(left, "↑ Higher TF (above) = HTF direction\nLower TF (above) = LTF entry/exit trigger",
               color=MUTED, font=("Segoe UI", 9)).pack(padx=16, anchor="w")

        # ── RIGHT: option discovery params ────────────────────────────────
        _label(right, "Option Discovery", font=FONT_MD).pack(pady=(14,8))

        self.var_expiry_mode  = self._combo(right, "Expiry Mode",
                                            ["AUTO","TRADING_DAY","CURRENT","NEXT","FAR"],
                                            default="AUTO")
        self.var_otm_steps    = self._combo(right, "OTM Steps to Try",
                                            ["3,4,5,6","3,4","4,5,6","3","4","5"],
                                            default="3,4,5,6")
        self.var_rollover     = self._combo(right, "Rollover Variant",
                                            ["1 — 3rd weekly expiry @ 3 PM",
                                             "2 — 4 trading days before expiry"],
                                            default="2 — 4 trading days before expiry")

        _label(right, "Premium Rules  (₹)", font=FONT_MD, color=MUTED).pack(pady=(16,4))

        self.var_min_short    = self._slider(right, "Min Short Premium", 100, 500, 200)
        self.var_max_short    = self._slider(right, "Max Short Premium", 100, 500, 300)
        self.var_min_hedge    = self._slider(right, "Min Hedge Premium", 100, 400, 100)
        self.var_max_hedge    = self._slider(right, "Max Hedge Premium", 100, 400, 200)
        self.var_min_credit   = self._slider(right, "Min Net Credit",    150, 400, 150)

        # ── Global Stop Loss ──────────────────────────────────────────────────
        sep2 = ctk.CTkFrame(right, fg_color=BORDER, height=1)
        sep2.pack(fill="x", padx=16, pady=(12, 6))

        gsl_row = ctk.CTkFrame(right, fg_color=CARD_BG, corner_radius=8)
        gsl_row.pack(fill="x", padx=16, pady=(0, 12))
        gsl_inner = ctk.CTkFrame(gsl_row, fg_color="transparent")
        gsl_inner.pack(fill="x", padx=12, pady=8)
        gsl_inner.columnconfigure(1, weight=1)

        _label(gsl_inner, "🛑  Global Daily SL (₹)",
               font=FONT_MD, color=RED).grid(row=0, column=0, sticky="w")
        self.var_global_sl = ctk.CTkEntry(
            gsl_inner, width=120,
            fg_color=PANEL_BG, border_color=RED,
            text_color=WHITE, placeholder_text_color=MUTED,
            placeholder_text="e.g. 5000",
        )
        self.var_global_sl.grid(row=0, column=1, sticky="e", padx=(8, 0))
        _label(right,
               "If day's realized+unrealized P&L drops below -₹ this value,\n"
               "close position and stop all trades for the day.",
               color=MUTED, font=("Segoe UI", 9)).pack(padx=16, anchor="w", pady=(0, 8))

    # ── helpers ────────────────────────────────────────────────────────────
    def _entry_field(self, parent, label, default="") -> ctk.CTkEntry:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        row.columnconfigure(1, weight=1)
        _label(row, label, color=MUTED).grid(row=0, column=0, sticky="w")
        e = ctk.CTkEntry(
            row, width=100,
            fg_color=PANEL_BG, border_color=BORDER,
            text_color=WHITE, placeholder_text_color=MUTED,
        )
        e.insert(0, default)
        e.grid(row=0, column=1, sticky="e", pady=2)
        return e

    def _combo(self, parent, label, values, default=None) -> ctk.CTkComboBox:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        row.columnconfigure(1, weight=1)
        _label(row, label, color=MUTED).grid(row=0, column=0, sticky="w")
        cb = ctk.CTkComboBox(
            row, values=values, width=220,
            fg_color=PANEL_BG, border_color=BORDER,
            button_color=ACCENT2, button_hover_color="#3451d1",
            text_color=WHITE, dropdown_fg_color=PANEL_BG,
        )
        cb.set(default or values[0])
        cb.grid(row=0, column=1, sticky="e", pady=2)
        return cb

    def _slider(self, parent, label, from_, to, default) -> ctk.CTkSlider:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        lbl = _label(row, f"{label}: {default}", color=MUTED)
        lbl.pack(anchor="w")
        sl = ctk.CTkSlider(
            row, from_=from_, to=to, number_of_steps=int(to-from_),
            fg_color=BORDER, progress_color=ACCENT2, button_color=WHITE,
            button_hover_color=ACCENT2,
        )
        sl.set(default)
        sl.pack(fill="x")

        def _upd(v):
            lbl.configure(text=f"{label}: {int(v)}")
        sl.configure(command=_upd)
        return sl

    def get_config(self) -> dict:
        def _variant_num(s):
            return int(str(s).split(" ")[0])
        def _rollover_num(s):
            return int(str(s).split(" ")[0])
        def _safe_int(s, default):
            try:
                v = int(str(s).strip())
                return v if v > 0 else default
            except Exception:
                return default
        def _safe_float(s, default):
            try:
                v = float(str(s).strip())
                return v if v > 0 else default
            except Exception:
                return default

        def _safe_positive(s, default):
            try:
                v = float(str(s).strip())
                return v if v > 0 else default
            except Exception:
                return default

        return {
            "timeframe":         int(self.var_timeframe.get()),
            "variant":           _variant_num(self.var_variant.get()),
            "st_period":         _safe_int(self.var_st_period.get(), 10),
            "st_multiplier":     _safe_float(self.var_st_mult.get(), 3.0),
            "sl_percent":        float(self.var_sl_pct.get()),
            "expiry_mode":       self.var_expiry_mode.get(),
            "otm_steps":         self.var_otm_steps.get(),
            "rollover_variant":  _rollover_num(self.var_rollover.get()),
            "lower_tf_minutes":  int(self.var_lower_tf.get()),
            "min_short_premium": float(self.var_min_short.get()),
            "max_short_premium": float(self.var_max_short.get()),
            "min_hedge_premium": float(self.var_min_hedge.get()),
            "max_hedge_premium": float(self.var_max_hedge.get()),
            "min_net_credit":    float(self.var_min_credit.get()),
            "global_sl_rupees":  _safe_positive(self.var_global_sl.get(), 0.0),
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_params(self) -> None:
        import json
        try:
            data = self.get_config()
            # Store variant and rollover as full display string for easy restore
            data["_variant_str"]   = self.var_variant.get()
            data["_rollover_str"]  = self.var_rollover.get()
            data["_expiry_mode"]   = self.var_expiry_mode.get()
            data["_otm_steps"]     = self.var_otm_steps.get()
            data["_global_sl"]     = self.var_global_sl.get().strip()
            self._params_path().write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )
            self._save_status.configure(
                text=f"✅ Saved to {self._params_path().name}", text_color=GREEN
            )
        except Exception as e:
            self._save_status.configure(text=f"❌ Save failed: {e}", text_color=RED)

    def _load_params(self) -> None:
        import json
        try:
            if not self._params_path().exists():
                self._save_status.configure(
                    text="⚠ No saved parameters found", text_color=YELLOW
                )
                return
            data = json.loads(self._params_path().read_text(encoding="utf-8"))
            self._apply_params(data)
            self._save_status.configure(
                text=f"✅ Loaded from {self._params_path().name}", text_color=GREEN
            )
        except Exception as e:
            self._save_status.configure(text=f"❌ Load failed: {e}", text_color=RED)

    def _apply_params(self, data: dict) -> None:
        """Apply a params dict to all GUI widgets."""
        try:
            tf = str(data.get("timeframe", "60"))
            if tf in [self.var_timeframe.cget("values") if hasattr(self.var_timeframe, "cget") else []]:
                pass
            self.var_timeframe.set(tf)
        except Exception:
            pass

        # Variant — prefer full string, fall back to number prefix match
        try:
            vs = data.get("_variant_str") or ""
            if not vs:
                vn = str(data.get("variant", "3"))
                for opt in ["1 — SL% + ST change","2 — ST change only",
                            "3 — Candle breach + ST change","4 — Candle breach + SL%",
                            "5 — Dual TF Trailing (HTF entry + LTF exit)"]:
                    if opt.startswith(vn + " "):
                        vs = opt
                        break
            if vs:
                self.var_variant.set(vs)
        except Exception:
            pass

        try:
            self.var_st_period.delete(0, "end")
            self.var_st_period.insert(0, str(data.get("st_period", "10")))
        except Exception:
            pass

        try:
            self.var_st_mult.delete(0, "end")
            self.var_st_mult.insert(0, str(data.get("st_multiplier", "3.0")))
        except Exception:
            pass

        try:
            self.var_sl_pct.set(float(data.get("sl_percent", 30)))
        except Exception:
            pass

        try:
            em = data.get("_expiry_mode") or data.get("expiry_mode", "AUTO")
            self.var_expiry_mode.set(str(em))
        except Exception:
            pass

        try:
            ots = data.get("_otm_steps") or data.get("otm_steps", "3,4,5,6")
            self.var_otm_steps.set(str(ots))
        except Exception:
            pass

        # Rollover — prefer full string
        try:
            rs = data.get("_rollover_str") or ""
            if not rs:
                rn = str(data.get("rollover_variant", "2"))
                for opt in ["1 — 3rd weekly expiry @ 3 PM",
                            "2 — 4 trading days before expiry"]:
                    if opt.startswith(rn + " "):
                        rs = opt
                        break
            if rs:
                self.var_rollover.set(rs)
        except Exception:
            pass

        try:
            self.var_lower_tf.set(str(data.get("lower_tf_minutes", "15")))
        except Exception:
            pass

        try:
            self.var_min_short.set(float(data.get("min_short_premium", 200)))
        except Exception:
            pass

        try:
            self.var_max_short.set(float(data.get("max_short_premium", 300)))
        except Exception:
            pass

        try:
            self.var_min_hedge.set(float(data.get("min_hedge_premium", 100)))
        except Exception:
            pass

        try:
            self.var_max_hedge.set(float(data.get("max_hedge_premium", 200)))
        except Exception:
            pass

        try:
            self.var_min_credit.set(float(data.get("min_net_credit", 150)))
        except Exception:
            pass

        try:
            gsl = data.get("_global_sl") or str(data.get("global_sl_rupees", ""))
            if gsl and str(gsl) != "0.0" and str(gsl) != "0":
                self.var_global_sl.delete(0, "end")
                self.var_global_sl.insert(0, str(gsl))
        except Exception:
            pass

    def _auto_load(self) -> None:
        """Silently load saved params on startup if file exists."""
        import json
        try:
            if self._params_path().exists():
                data = json.loads(self._params_path().read_text(encoding="utf-8"))
                self._apply_params(data)
                self._save_status.configure(
                    text="✅ Parameters auto-loaded", text_color=GREEN
                )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Tab 3 — Live Dashboard
# ─────────────────────────────────────────────────────────────────────────────
class DashboardTab(ctk.CTkFrame):
    def __init__(self, parent, bridge: StrategyBridge):
        super().__init__(parent, fg_color="transparent")
        self._bridge = bridge
        self._build()
        self._refresh()

    def _build(self):
        # ── top row: status cards ─────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(8,4))
        for i in range(6):
            top.columnconfigure(i, weight=1)

        def stat_card(col, title):
            f = _card(top)
            f.grid(row=0, column=col, sticky="ew", padx=4)
            _label(f, title, color=MUTED, font=("Segoe UI",9)).pack(pady=(6,0))
            v = _label(f, "-", font=("Segoe UI",14,"bold"))
            v.pack(pady=(0,6))
            return v

        self._lbl_ltp     = stat_card(0, "NIFTY LTP")
        self._lbl_trend   = stat_card(1, "ST Trend")
        self._lbl_signal  = stat_card(2, "Flip Signal")
        self._lbl_active  = stat_card(3, "Active Position")
        self._lbl_pnl_pts = stat_card(4, "P&L (pts)")
        self._lbl_pnl_rs  = stat_card(5, "P&L (₹)")

        # ── Global SL banner (hidden until triggered) ─────────────────────────
        self._gsl_banner = ctk.CTkFrame(
            self, fg_color="#7f1d1d", corner_radius=8, height=36
        )
        # not packed yet — only shown when triggered
        self._gsl_banner_lbl = _label(
            self._gsl_banner,
            "🛑  GLOBAL SL HIT — Position Closed. No new trades today.",
            font=("Segoe UI", 13, "bold"), color=WHITE,
        )
        self._gsl_banner_lbl.pack(expand=True, pady=8)
        self._gsl_banner_visible = False

        # ── middle: candles + option setup side by side ───────────────────
        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.pack(fill="both", expand=True, padx=12, pady=4)
        mid.columnconfigure(0, weight=2)
        mid.columnconfigure(1, weight=3)

        # candle table
        ccard = _card(mid)
        ccard.grid(row=0, column=0, sticky="nsew", padx=(0,6))
        _label(ccard, "Recent Candles", font=FONT_MD).pack(pady=(10,4))

        self._candle_box = ctk.CTkTextbox(
            ccard, fg_color=PANEL_BG, text_color=WHITE, font=FONT_MONO,
            border_color=BORDER, wrap="none",
        )
        self._candle_box.pack(fill="both", expand=True, padx=8, pady=(0,8))

        # option + paper block
        ocard = _card(mid)
        ocard.grid(row=0, column=1, sticky="nsew", padx=(6,0))
        _label(ocard, "Option Setup & Paper Position", font=FONT_MD).pack(pady=(10,4))

        self._option_box = ctk.CTkTextbox(
            ocard, fg_color=PANEL_BG, text_color=WHITE, font=FONT_MONO,
            border_color=BORDER, wrap="word",
        )
        self._option_box.pack(fill="both", expand=True, padx=8, pady=(0,8))

        # ── bottom: event log ─────────────────────────────────────────────
        ecard = _card(self)
        ecard.pack(fill="x", padx=12, pady=(0,8))

        evhdr = ctk.CTkFrame(ecard, fg_color="transparent")
        evhdr.pack(fill="x", padx=12, pady=(8,2))
        _label(evhdr, "Strategy Events", font=FONT_MD).pack(side="left")
        self._log_path_lbl = _label(evhdr, "Log: not started", color=MUTED,
                                    font=("Segoe UI", 9))
        self._log_path_lbl.pack(side="right")
        self._trade_log_lbl = _label(evhdr, "", color=MUTED,
                                     font=("Segoe UI", 9))
        self._trade_log_lbl.pack(side="right", padx=(0, 12))

        self._event_box = ctk.CTkTextbox(
            ecard, height=160, fg_color=PANEL_BG, text_color=MUTED,
            font=FONT_MONO, border_color=BORDER, wrap="word",
        )
        self._event_box.pack(fill="x", padx=8, pady=(0,8))

    def _refresh(self):
        if not self.winfo_exists():
            return
        try:
            self._update_ui()
        except Exception:
            pass
        self.after(1000, self._refresh)

    def _update_ui(self):
        snap    = self._bridge.get_snapshot()
        events  = self._bridge.get_events()

        st  = snap.get("st") or {}
        sig = snap.get("signal") or {}
        opt = snap.get("option") or {}
        pap = snap.get("paper") or {}
        pos = pap.get("active_position") or {}

        ltp    = snap.get("ltp")
        trend  = st.get("trend") or sig.get("trend") or "-"
        signal = sig.get("signal") or "-"
        active = sig.get("active") or "-"

        # top cards
        self._lbl_ltp.configure(text=_fmt(ltp), text_color=WHITE)
        self._lbl_trend.configure(
            text=trend,
            text_color=GREEN if trend == "BUY" else (RED if trend == "SELL" else MUTED)
        )
        self._lbl_signal.configure(
            text=signal,
            text_color=GREEN if signal == "BUY" else (RED if signal == "SELL" else MUTED)
        )
        self._lbl_active.configure(
            text=active,
            text_color=YELLOW if active not in ("-", None) else MUTED
        )

        pnl_pts = pos.get("pnl_points")
        pnl_rs  = pos.get("pnl_rupees")
        if pnl_pts is not None:
            c = GREEN if float(pnl_pts) >= 0 else RED
            self._lbl_pnl_pts.configure(text=f"{float(pnl_pts):+.2f}", text_color=c)
            self._lbl_pnl_rs.configure(text=f"₹{float(pnl_rs or 0):+.0f}", text_color=c)
        else:
            self._lbl_pnl_pts.configure(text="-", text_color=MUTED)
            self._lbl_pnl_rs.configure(text="-", text_color=MUTED)

        # ── Global SL banner ──────────────────────────────────────────────────
        gsl_hit = any("GLOBAL SL HIT" in str(ev) for ev in (events or []))
        if gsl_hit and not self._gsl_banner_visible:
            self._gsl_banner.pack(fill="x", padx=12, pady=(0, 4))
            self._gsl_banner_visible = True
        elif not gsl_hit and self._gsl_banner_visible:
            # Hide on new day (events list won't have yesterday's events)
            self._gsl_banner.pack_forget()
            self._gsl_banner_visible = False

        # candle table
        self._candle_box.configure(state="normal")
        self._candle_box.delete("0.0", "end")
        hdr = f"{'Time':>6}  {'Open':>10}  {'High':>10}  {'Low':>10}  {'Close':>10}  {'Ticks':>5}\n"
        self._candle_box.insert("end", hdr)
        self._candle_box.insert("end", "─" * 60 + "\n")
        for c in (snap.get("recent_closed") or [])[:8]:
            t = _fmt_epoch(c.get("bucket", c.get("epoch", c.get("time"))))
            self._candle_box.insert(
                "end",
                f"{t:>6}  {_fmt(c.get('open')):>10}  {_fmt(c.get('high')):>10}  "
                f"{_fmt(c.get('low')):>10}  {_fmt(c.get('close')):>10}  "
                f"{str(c.get('ticks','-')):>5}\n"
            )
        self._candle_box.configure(state="disabled")

        # option + paper block
        self._option_box.configure(state="normal")
        self._option_box.delete("0.0", "end")

        def line(s): self._option_box.insert("end", s + "\n")

        # Variant 5: show dual-TF state at top
        variant_num = sig.get("variant") or 0
        if int(variant_num) == 5:
            htf_trend  = sig.get("htf_trend")  or "-"
            ltf_trend  = sig.get("ltf_trend")  or "-"
            htf_st     = sig.get("htf_st")
            ltf_st     = sig.get("ltf_st")
            htf_armed  = sig.get("htf_armed",  False)
            ltf_armed  = sig.get("ltf_armed",  False)
            waiting    = sig.get("waiting_reentry", False)
            line(f"Variant 5 — Dual TF Trailing")
            line(f"HTF Trend = {htf_trend}  ST={_fmt(htf_st)}  Armed={'YES' if htf_armed else 'NO'}")
            line(f"LTF Trend = {ltf_trend}  ST={_fmt(ltf_st)}  Armed={'YES' if ltf_armed else 'NO'}")
            line(f"Active    = {sig.get('active') or '-'}  Waiting Re-entry={'YES' if waiting else 'NO'}")
            line("─" * 54)

        exp_cur = opt.get("expiry_current") or "-"
        exp_nxt = opt.get("expiry_next")    or "-"
        exp_far = opt.get("expiry_far")     or "-"
        line(f"Expiries  | current={exp_cur} | next={exp_nxt} | far={exp_far}")
        line("─" * 54)

        if opt.get("has_setup"):
            line(f"Setup     | trend={opt.get('trend')} | expiry={opt.get('expiry_text')}")
            line(f"OTM       | step={opt.get('otm_step')} | hedge_steps={opt.get('hedge_distance_steps')}")
            line(f"Short Leg | {opt.get('short_symbol')} @ {_fmt(opt.get('short_premium'))}")
            line(f"Hedge Leg | {opt.get('hedge_symbol')} @ {_fmt(opt.get('hedge_premium'))}")
            line(f"Net Credit| {_fmt(opt.get('net_credit'))}")
        else:
            line("Setup     | No valid setup yet")

        line("─" * 54)
        if pap.get("has_active_position"):
            sl_hit = "  ⚠ SL HIT" if pos.get("short_sl_hit") else ""
            line(f"Position  | OPEN | trend={pos.get('trend')} | entry={pos.get('entry_time')}{sl_hit}")
            line(f"Entry     | short @ {_fmt(pos.get('short_entry_premium'))} | hedge @ {_fmt(pos.get('hedge_entry_premium'))} | net={_fmt(pos.get('net_credit_entry'))}")
            line(f"Live MTM  | short={_fmt(pos.get('short_ltp'))} | hedge={_fmt(pos.get('hedge_ltp'))} | net_now={_fmt(pos.get('net_premium_now'))}")
            line(f"P&L       | pts={_fmt(pnl_pts)} | ₹={_fmt(pnl_rs,0)} | SL price={_fmt(pos.get('short_sl_price'))}")
            line(f"Extremes  | max_profit={_fmt(pos.get('max_profit_seen'))} | max_loss={_fmt(pos.get('max_loss_seen'))}")
        else:
            line("Position  | No active paper position")

        if pap.get("last_event"):
            line(f"\nLast event: {pap.get('last_event')}")

        self._option_box.configure(state="disabled")

        # event log
        self._event_box.configure(state="normal")
        self._event_box.delete("0.0", "end")
        for ev in (events or [])[:30]:
            self._event_box.insert("end", ev + "\n")
        self._event_box.configure(state="disabled")

        # update log file path label from events
        try:
            for ev in (events or []):
                if "Activity log:" in ev or "Log file:" in ev:
                    path = ev.split(":")[-1].strip()
                    self._log_path_lbl.configure(text=f"Log: {path}", text_color=GREEN)
                if "Trade log:" in ev:
                    path = ev.split("Trade log:")[-1].strip()
                    self._trade_log_lbl.configure(text=f"Trades: {path}", text_color=GREEN)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Main Application Window
# ─────────────────────────────────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self._bridge = StrategyBridge()
        self.title("Balfund — Supertrend Option Seller")
        self.geometry("1280x840")
        self.minsize(1100, 700)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color=DARK_BG)
        self._build()

    def _build(self):
        # ── header ────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color=PANEL_BG, height=52, corner_radius=0)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        _label(header, "  BALFUND", font=("Segoe UI", 16, "bold"), color=ACCENT).pack(side="left", padx=8)
        _label(header, "Supertrend Option Selling Strategy", font=FONT_MD, color=MUTED).pack(side="left")

        # status + start/stop
        self._status_lbl = _label(header, "●  STOPPED", color=RED, font=FONT_MD)
        self._status_lbl.pack(side="right", padx=12)

        self._btn_stop = ctk.CTkButton(
            header, text="■  Stop", command=self._stop,
            fg_color="#7f1d1d", hover_color=RED, width=100, height=32,
        )
        self._btn_stop.pack(side="right", padx=4)
        self._btn_stop.configure(state="disabled")

        self._btn_start = ctk.CTkButton(
            header, text="▶  Start", command=self._start,
            fg_color="#14532d", hover_color=GREEN, width=100, height=32,
        )
        self._btn_start.pack(side="right", padx=4)

        # ── tabs ──────────────────────────────────────────────────────────
        tabs = ctk.CTkTabview(
            self, fg_color=DARK_BG,
            segmented_button_fg_color=PANEL_BG,
            segmented_button_selected_color=ACCENT,
            segmented_button_unselected_color=PANEL_BG,
            segmented_button_selected_hover_color="#c0392b",
            text_color=WHITE,
        )
        tabs.pack(fill="both", expand=True)

        tabs.add("🔑  Token Manager")
        tabs.add("⚙  Strategy Setup")
        tabs.add("📈  Live Dashboard")

        self._token_tab   = TokenTab(tabs.tab("🔑  Token Manager"), on_token_saved=self._on_token_saved)
        self._token_tab.pack(fill="both", expand=True)

        self._setup_tab   = SetupTab(tabs.tab("⚙  Strategy Setup"))
        self._setup_tab.pack(fill="both", expand=True)

        self._dash_tab    = DashboardTab(tabs.tab("📈  Live Dashboard"), self._bridge)
        self._dash_tab.pack(fill="both", expand=True)

        # auto-refresh status
        self._check_status()

    def _on_token_saved(self, client_id, token):
        pass   # credentials flow into bridge at Start time

    def _start(self):
        creds = self._token_tab.get_credentials()

        # If no client_id in GUI, try shared token file automatically
        if not creds["client_id"]:
            try:
                from dhan_token_manager import read_shared_token
                shared = read_shared_token()
                if shared.get("client_id") and shared.get("access_token"):
                    creds["client_id"]    = shared["client_id"]
                    creds["access_token"] = shared["access_token"]
            except Exception:
                pass

        if not creds["client_id"]:
            from tkinter import messagebox
            messagebox.showwarning(
                "Missing Credentials",
                "Please enter your Dhan credentials in the Token Manager tab,\n"
                "or run dhan-token-generator.exe first."
            )
            return

        cfg = self._setup_tab.get_config()
        cfg.update(creds)

        result = self._bridge.start(cfg)
        if result == "OK":
            self._btn_start.configure(state="disabled")
            self._btn_stop.configure(state="normal")

    def _stop(self):
        self._bridge.stop()
        self._btn_start.configure(state="normal")
        self._btn_stop.configure(state="disabled")

    def _check_status(self):
        if self._bridge.running:
            self._status_lbl.configure(text="●  RUNNING", text_color=GREEN)
        else:
            self._status_lbl.configure(text="●  STOPPED", text_color=RED)
        self.after(1000, self._check_status)

    def on_closing(self):
        self._bridge.stop()
        time.sleep(0.3)
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()


if __name__ == "__main__":
    main()
