from __future__ import annotations

import signal
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from candle_builder import TimeframeCandleBuilder
from config import build_arg_parser, load_config
from dashboard import DashboardPrinter, DashboardState
from dhan_ws_client import DhanWSClient
from history_loader import fetch_intraday_1m_history
from option_chain_engine import OptionChainEngine
from option_signal_bridge import OptionDiscoveryConfig, OptionSignalBridge
from option_ws_client import OptionWSClient
from paper_positions import PaperPositionManager
from rollover_engine import (
    NSE_HOLIDAYS,
    should_rollover_v1_now,
    should_rollover_v2_now,
    rollover_info,
)
from signal_engine import SignalEngine
from state_manager import StateManager
from supertrend_engine import SupertrendEngine
from token_helper import ensure_dhan_token


def _get_candle_epoch(candle: dict) -> int | None:
    for key in ("bucket", "epoch", "start_epoch", "ts", "timestamp", "time"):
        value = candle.get(key)
        if value is not None:
            try:
                return int(value)
            except Exception:
                return None
    return None


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    cfg = load_config(args)

    client_id, access_token = ensure_dhan_token()

    state_manager = StateManager(
        cfg.state_file,
        cfg.timeframe_minutes,
        cfg.st_period,
        cfg.st_multiplier,
    )

    candle_builder = TimeframeCandleBuilder(cfg.timeframe_minutes)
    st_engine = SupertrendEngine(cfg.st_period, cfg.st_multiplier)
    signal_engine = SignalEngine(cfg.variant, cfg.sl_percent, state_manager)

    option_chain_engine = OptionChainEngine(
        client_id=client_id,
        access_token=access_token,
        underlying_scrip=int(cfg.security_id),
        underlying_seg=cfg.exchange_segment,
        atm_step=100,                   # strategy: ATM rounded to nearest 100
        strike_step=cfg.option_strike_step,
    )
    option_bridge = OptionSignalBridge(
        option_chain_engine=option_chain_engine,
        discovery_cfg=OptionDiscoveryConfig(
            strike_step=cfg.option_strike_step,
            otm_steps=tuple(cfg.option_otm_steps),
            expiry_mode=cfg.option_expiry_mode,
            min_short_premium=cfg.min_short_premium,
            max_short_premium=cfg.max_short_premium,
            min_hedge_premium=cfg.min_hedge_premium,
            max_hedge_premium=cfg.max_hedge_premium,
            min_net_credit=cfg.min_net_credit,
        ),
    )

    paper_manager = PaperPositionManager()

    option_ws = OptionWSClient(
        client_id=client_id,
        access_token=access_token,
        on_tick=lambda sid, ltp, ltt: _on_option_tick(sid, ltp, ltt),
        on_status=lambda msg: dash_state.add_event(f"OptionWS: {msg}"),
    )

    dash_state = DashboardState(
        symbol=cfg.symbol_name,
        timeframe_minutes=cfg.timeframe_minutes,
        variant=cfg.variant,
        st_period=cfg.st_period,
        st_multiplier=cfg.st_multiplier,
    )
    dashboard = DashboardPrinter(dash_state, refresh_seconds=1.0)
    dashboard_thread = threading.Thread(target=dashboard.run, daemon=True)
    dashboard_thread.start()

    stop_event = threading.Event()

    # ── IST time helpers ────────────────────────────────────────────────────
    _IST = timezone(timedelta(hours=5, minutes=30))

    def _ist_now() -> datetime:
        return datetime.now(tz=_IST)

    def _is_after_1015() -> bool:
        n = _ist_now()
        return n.hour > 10 or (n.hour == 10 and n.minute >= 15)

    def _is_930_window() -> bool:
        n = _ist_now()
        return n.hour == 9 and n.minute == 30

    # ── Per-session trackers ────────────────────────────────────────────────
    _v1_exit_today: bool = False          # Variant 1: position closed intraday
    _930_check_done_date = None           # date of last 9:30 AM SL check
    _rollover_done_date = None            # date of last rollover execution

    last_seen_flip_signal = None
    last_seen_active_state = None
    bootstrap_discovery_done = False
    last_discovery_candle_epoch = None

    def refresh_dashboard(*, ltp=None, ltt_epoch=None) -> None:
        snap = candle_builder.snapshot()
        dash_state.update_market(
            ltp=ltp if ltp is not None else snap.get("last_ltp"),
            ltt_epoch=ltt_epoch if ltt_epoch is not None else snap.get("last_ltt_epoch"),
            current_candle=snap.get("current"),
            recent_closed=snap.get("history", []),
            st_snapshot=st_engine.snapshot(),
            signal_snapshot=signal_engine.snapshot(),
            option_snapshot=option_bridge.snapshot(),
            paper_snapshot=paper_manager.snapshot(),
        )

    def _on_option_tick(security_id: str, ltp: float, ltt_epoch: int) -> None:
        nonlocal _v1_exit_today
        sl_event = paper_manager.update_live_quote(security_id, ltp, ltt_epoch)
        if sl_event:
            dash_state.add_event(sl_event)
            # Variants 1 & 4: auto-close position immediately when SL hit on live tick
            if cfg.variant in (1, 4) and paper_manager.has_active():
                pos_snap = paper_manager.snapshot().get("active_position") or {}
                if pos_snap.get("short_sl_hit"):
                    reason = f"Variant {cfg.variant}: short premium SL breached on live tick"
                    msg = paper_manager.close_active_position(reason=reason)
                    dash_state.add_event(msg)
                    _sync_option_ws()
                    if cfg.variant == 1:
                        _v1_exit_today = True
        refresh_dashboard()

    def _sync_option_ws() -> None:
        """Keep OptionWSClient watchlist in sync with active paper position."""
        instruments = paper_manager.active_option_watchlist()
        option_ws.set_instruments(instruments)

    def _check_930_sl_v1() -> None:
        """
        Variant 1 only: at 9:30 AM check whether the short premium has already
        exceeded the SL price (gap-up scenario). If so, close immediately.
        """
        nonlocal _v1_exit_today
        if cfg.variant != 1:
            return
        if not paper_manager.has_active():
            return
        pos_snap = paper_manager.snapshot().get("active_position") or {}
        if pos_snap.get("short_sl_hit"):
            reason = "Variant 1: 9:30 AM check - short premium already above SL at open"
            msg = paper_manager.close_active_position(reason=reason)
            dash_state.add_event(msg)
            _sync_option_ws()
            _v1_exit_today = True

    def _do_rollover() -> None:
        """Close current paper position and re-enter for the new expiry."""
        if paper_manager.has_active():
            msg = paper_manager.close_active_position("Rollover: closing expiring month position")
            dash_state.add_event(msg)
            _sync_option_ws()

        snap = candle_builder.snapshot()
        spot_price: Optional[float] = None
        current_candle = snap.get("current")
        if current_candle and current_candle.get("close") is not None:
            spot_price = float(current_candle["close"])
        elif dash_state.last_ltp is not None:
            spot_price = float(dash_state.last_ltp)
        elif snap.get("history"):
            spot_price = float(snap["history"][0]["close"])

        if spot_price is None:
            dash_state.add_event("Rollover: no spot price available, skipping re-entry")
            return

        trend = _current_actionable_trend()
        if trend not in ("BUY", "SELL"):
            dash_state.add_event("Rollover: no actionable trend, skipping re-entry")
            return

        _attempt_option_discovery(
            spot_price=float(spot_price),
            trend=trend,
            event_prefix=f"Rollover V{cfg.rollover_variant} re-entry",
        )

    def _maybe_check_rollover() -> None:
        """Check rollover conditions once per day around 3 PM IST."""
        nonlocal _rollover_done_date
        now = _ist_now()
        if now.hour < 15 or now.hour > 15:
            return
        today = now.date()
        if _rollover_done_date == today:
            return

        expiry_list = option_bridge.last_expiry_info.get("all") or []
        if not expiry_list:
            return

        triggered = False
        if cfg.rollover_variant == 1:
            triggered = should_rollover_v1_now(expiry_list)
        elif cfg.rollover_variant == 2:
            triggered = should_rollover_v2_now(expiry_list, NSE_HOLIDAYS)

        if triggered:
            _rollover_done_date = today
            dash_state.add_event(f"Rollover V{cfg.rollover_variant} triggered @ {now.strftime('%H:%M')} IST")
            _do_rollover()

    def on_status(msg: str) -> None:
        lower = msg.lower()
        connected = None
        err = None

        if "subscribed" in lower or "connected" in lower:
            connected = True
        elif "closed" in lower or "disconnected" in lower:
            connected = False

        if "error" in lower or "exception" in lower or "failed" in lower:
            err = msg

        dash_state.update_status(msg, connected=connected, error=err)
        dash_state.add_event(msg)

    def bootstrap_history() -> None:
        candles_1m, hist_msg = fetch_intraday_1m_history(
            client_id=client_id,
            access_token=access_token,
            security_id=cfg.security_id,
            exchange_segment=cfg.exchange_segment,
            lookback_days=cfg.history_lookback_days,
            limit=cfg.history_limit,
            instrument="INDEX",
        )
        dash_state.add_event(hist_msg)

        if not candles_1m:
            dash_state.add_event("History unavailable. Continuing with live warmup.")
            refresh_dashboard()
            return

        seeded_tf = candle_builder.seed_from_1m_history(candles_1m)
        dash_state.add_event(f"Aggregated {seeded_tf} seeded timeframe candles")

        snap = candle_builder.snapshot()
        history = list(reversed(snap.get("history", [])))

        for candle in history:
            st_result = st_engine.update(candle)
            signal_engine.process_historical_candle(candle, st_result)

        state_manager.mark_indicator_seeded()
        dash_state.add_event("Indicator warmup complete from history")
        refresh_dashboard()

    def bootstrap_option_chain() -> None:
        try:
            msg = option_bridge.refresh_master(force_refresh=False)
            dash_state.add_event(msg)
        except Exception as e:
            dash_state.add_event(f"Option chain bootstrap failed: {e}")
        refresh_dashboard()

    def _trend_from_active(active_state: str | None) -> str | None:
        if active_state == "SHORT_PUT":
            return "BUY"
        if active_state == "SHORT_CALL":
            return "SELL"
        return None

    def _current_actionable_trend() -> str | None:
        sig_snap = signal_engine.snapshot()
        active_trend = _trend_from_active(sig_snap.get("active"))
        if active_trend in ("BUY", "SELL"):
            return active_trend

        current_trend = str(sig_snap.get("trend") or "").upper().strip()
        if current_trend in ("BUY", "SELL"):
            if sig_snap.get("signal_candle_high") is not None and sig_snap.get("signal_candle_low") is not None:
                return current_trend
        return None

    def _attempt_option_discovery(*, spot_price: float, trend: str, event_prefix: str, candle_epoch=None) -> None:
        nonlocal last_discovery_candle_epoch
        setup, reason = option_bridge.discover_setup(
            spot_price=float(spot_price),
            trend=str(trend),
        )
        dash_state.add_event(f"{event_prefix} | {reason}")

        if setup is not None:
            msg = paper_manager.open_position(setup, spot_price=float(spot_price), sl_percent=cfg.sl_percent)
            dash_state.add_event(msg)
            _sync_option_ws()

        if candle_epoch is not None:
            last_discovery_candle_epoch = int(candle_epoch)
        refresh_dashboard(ltp=spot_price)

    def maybe_bootstrap_option_discovery() -> None:
        nonlocal bootstrap_discovery_done, last_seen_active_state

        if bootstrap_discovery_done:
            return

        trend = _current_actionable_trend()
        option_snap = option_bridge.snapshot()

        if trend is None:
            return
        if paper_manager.has_active():
            bootstrap_discovery_done = True
            return
        if option_snap.get("has_setup"):
            bootstrap_discovery_done = True
            return

        snap = candle_builder.snapshot()
        spot_price = None

        current_candle = snap.get("current")
        if current_candle and current_candle.get("close") is not None:
            spot_price = float(current_candle["close"])
        elif dash_state.last_ltp is not None:
            spot_price = float(dash_state.last_ltp)
        else:
            recent = snap.get("history", [])
            if recent:
                spot_price = float(recent[0]["close"])

        if spot_price is None:
            return

        _attempt_option_discovery(
            spot_price=float(spot_price),
            trend=trend,
            event_prefix="Bootstrap discovery",
        )
        last_seen_active_state = signal_engine.snapshot().get("active")
        bootstrap_discovery_done = True

    def maybe_run_option_discovery(spot_price: float, candle_epoch: int | None) -> None:
        nonlocal last_seen_flip_signal

        # Variant 1: no re-entry before 10:15 AM after an intraday exit
        if cfg.variant == 1 and _v1_exit_today and not _is_after_1015():
            return

        sig_snap = signal_engine.snapshot()
        flip_signal = sig_snap.get("signal")

        if flip_signal in ("BUY", "SELL") and flip_signal != last_seen_flip_signal:
            _attempt_option_discovery(
                spot_price=float(spot_price),
                trend=str(flip_signal),
                event_prefix="Flip discovery",
                candle_epoch=candle_epoch,
            )
            last_seen_flip_signal = flip_signal
        elif flip_signal in (None, "", "-"):
            last_seen_flip_signal = None

    def maybe_run_flat_rescan(spot_price: float, candle_epoch: int | None) -> None:
        if candle_epoch is None:
            return
        if paper_manager.has_active():
            return

        # Variant 1: no re-entry before 10:15 AM after an intraday exit
        if cfg.variant == 1 and _v1_exit_today and not _is_after_1015():
            return

        sig_snap = signal_engine.snapshot()
        if sig_snap.get("waiting_reentry"):
            return

        trend = _current_actionable_trend()
        if trend not in ("BUY", "SELL"):
            return

        if last_discovery_candle_epoch == int(candle_epoch):
            return

        _attempt_option_discovery(
            spot_price=float(spot_price),
            trend=trend,
            event_prefix="Flat rescan",
            candle_epoch=candle_epoch,
        )

    def maybe_sync_paper_close() -> None:
        nonlocal last_seen_active_state, _v1_exit_today

        sig_snap = signal_engine.snapshot()
        current_active = sig_snap.get("active")
        current_last_event = sig_snap.get("last_event")

        if last_seen_active_state in ("SHORT_PUT", "SHORT_CALL") and current_active is None:
            if paper_manager.has_active():
                reason = current_last_event or "Spot-side lifecycle exit"
                msg = paper_manager.close_active_position(reason=reason)
                dash_state.add_event(msg)
                _sync_option_ws()
                if cfg.variant == 1:
                    _v1_exit_today = True

        last_seen_active_state = current_active

    def on_tick(price: float, ltt_epoch: int) -> None:
        try:
            closed_candles = candle_builder.on_tick(price, ltt_epoch)

            refresh_dashboard(ltp=price, ltt_epoch=ltt_epoch)

            # Variant 1: 9:30 AM SL check (once per day)
            nonlocal _930_check_done_date, _v1_exit_today
            today = _ist_now().date()
            if _is_930_window() and _930_check_done_date != today:
                _930_check_done_date = today
                _v1_exit_today = False  # reset 10:15 gate at start of new day
                _check_930_sl_v1()

            # Rollover check @ 3 PM
            _maybe_check_rollover()

            maybe_bootstrap_option_discovery()

            for candle in closed_candles:
                st_result = st_engine.update(candle)
                events = signal_engine.process_live_closed_candle(candle, st_result)

                for event in events:
                    dash_state.add_event(event)

                maybe_sync_paper_close()

                candle_close = float(candle["close"])
                candle_epoch = _get_candle_epoch(candle)
                maybe_run_option_discovery(spot_price=candle_close, candle_epoch=candle_epoch)
                maybe_run_flat_rescan(spot_price=candle_close, candle_epoch=candle_epoch)

                refresh_dashboard(ltp=price, ltt_epoch=ltt_epoch)

        except Exception as e:
            msg = f"Tick processing error: {e}"
            dash_state.update_status(msg, error=msg)
            dash_state.add_event(msg)

    ws_client = DhanWSClient(
        client_id=client_id,
        access_token=access_token,
        exchange_segment=cfg.exchange_segment,
        security_id=cfg.security_id,
        on_tick=on_tick,
        on_status=on_status,
    )

    def shutdown(*_args):
        if stop_event.is_set():
            return
        stop_event.set()

        try:
            dash_state.add_event("Stopping application...")
        except Exception:
            pass

        try:
            ws_client.stop()
        except Exception:
            pass

        try:
            option_ws.stop()
        except Exception:
            pass

        try:
            dashboard.stop()
        except Exception:
            pass

        time.sleep(0.5)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    dash_state.add_event(
        f"Starting Phase 4 dashboard | timeframe={cfg.timeframe_minutes}m | "
        f"variant={cfg.variant} | ST=({cfg.st_period},{cfg.st_multiplier}) | "
        f"expiry_mode={cfg.option_expiry_mode} | otm_steps={cfg.option_otm_steps} | "
        f"rollover_variant={cfg.rollover_variant} | source=OptionChain+AutoRescan"
    )

    bootstrap_history()
    bootstrap_option_chain()

    # Log rollover dates after bootstrap
    expiry_list = option_bridge.last_expiry_info.get("all") or []
    if expiry_list:
        dash_state.add_event(rollover_info(expiry_list))

    maybe_bootstrap_option_discovery()

    option_ws.start()
    ws_client.start()

    try:
        while not stop_event.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
