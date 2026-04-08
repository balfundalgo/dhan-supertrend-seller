from __future__ import annotations

from typing import Any, Dict, List, Optional


def _get_candle_epoch(candle: Dict[str, Any]) -> int:
    for key in ("bucket", "epoch", "start_epoch", "ts", "timestamp", "time"):
        if key in candle and candle[key] is not None:
            return int(candle[key])
    raise KeyError(
        "No candle epoch field found. Expected one of: bucket, epoch, start_epoch, ts, timestamp, time"
    )


class SignalEngine:
    def __init__(self, variant: int, sl_percent: float, state_manager) -> None:
        self.variant = int(variant)
        self.sl_percent = float(sl_percent)
        self.state_manager = state_manager

        self.current_trend: Optional[str] = None
        self.current_flip_signal: Optional[str] = None
        self.current_st_value: Optional[float] = None
        self.current_atr: Optional[float] = None

        # Variant 5: dual-TF engine (set externally by main.py after construction)
        self.dual_tf_engine = None

    def _active_from_trend(self, trend: Optional[str]) -> Optional[str]:
        if trend == "BUY":
            return "SHORT_PUT"
        if trend == "SELL":
            return "SHORT_CALL"
        return None

    def process_historical_candle(self, candle: Dict[str, Any], st_result: Dict[str, Any]) -> None:
        candle_epoch = _get_candle_epoch(candle)
        candle_high = float(candle["high"])
        candle_low = float(candle["low"])

        self.current_trend = st_result.get("trend")
        self.current_flip_signal = st_result.get("signal")
        self.current_st_value = st_result.get("supertrend")
        self.current_atr = st_result.get("atr")

        if self.current_flip_signal in ("BUY", "SELL"):
            self.state_manager.set_signal(
                signal=str(self.current_flip_signal),
                candle_high=candle_high,
                candle_low=candle_low,
                candle_epoch=candle_epoch,
                active=self._active_from_trend(self.current_flip_signal),
            )

        self.state_manager.set_last_closed_candle(candle_epoch)

    def reconcile_startup_state(self, bootstrap_current_trend: bool = True) -> Optional[str]:
        """
        Seed signal candle high/low from history so exit conditions work correctly.
        Does NOT set active=SHORT_PUT/CALL — that would cause phantom exit triggers
        if the reconciled signal candle low/high is breached before a real position opens.
        _actionable_trend() uses trend directly so active is not needed for entry.
        """
        state = self.state_manager.state

        if self.current_trend not in ("BUY", "SELL"):
            return None

        prev_active   = state.active
        prev_signal   = state.last_signal
        prev_waiting  = state.waiting_reentry

        # Clear stale opposite-side carryover
        if prev_signal != self.current_trend:
            self.state_manager.clear_signal_context()

        if bootstrap_current_trend:
            # Seed sig_hi/lo from history BUT force active=None
            # seed_trend_context skips None active, so call set_active separately
            self.state_manager.seed_trend_context(
                trend=str(self.current_trend),
                active=None,
                candle_high=state.signal_candle_high,
                candle_low=state.signal_candle_low,
                candle_epoch=state.signal_candle_epoch,
            )
            # Explicitly force active to None — prevents phantom exit triggers
            self.state_manager.set_active(None)
            # Clear waiting_reentry from any previous session state
            self.state_manager.set_waiting_reentry(False)
        else:
            self.state_manager.clear_lifecycle(keep_last_event=False)

        reason = (
            f"Startup reconcile: trend={self.current_trend} | "
            f"sig_hi={state.signal_candle_high} | sig_lo={state.signal_candle_low} | "
            f"prev_active={prev_active or '-'} | prev_waiting={'YES' if prev_waiting else 'NO'} | "
            f"active kept=None (phantom exit prevention)"
        )
        return reason

    def process_live_closed_candle(self, candle: Dict[str, Any], st_result: Dict[str, Any]) -> List[str]:
        events: List[str] = []

        candle_epoch = _get_candle_epoch(candle)
        candle_high = float(candle["high"])
        candle_low = float(candle["low"])
        candle_close = float(candle["close"])

        trend = st_result.get("trend")
        flip_signal = st_result.get("signal")
        st_value = st_result.get("supertrend")
        atr = st_result.get("atr")

        self.current_trend = trend
        self.current_flip_signal = flip_signal
        self.current_st_value = st_value
        self.current_atr = atr

        state = self.state_manager.state

        if not state.live_action_armed:
            self.state_manager.arm_live_actions()
            self.state_manager.set_last_closed_candle(candle_epoch)
            self.state_manager.set_last_event("Live action armed on first fresh candle close")
            events.append("Live action armed")
            return events

        if flip_signal in ("BUY", "SELL"):
            active = self._active_from_trend(flip_signal)
            self.state_manager.set_signal(
                signal=str(flip_signal),
                candle_high=candle_high,
                candle_low=candle_low,
                candle_epoch=candle_epoch,
                active=active,
            )
            self.state_manager.set_last_closed_candle(candle_epoch)

            msg = (
                f"Fresh {flip_signal} flip | "
                f"Signal candle H={candle_high:.2f} L={candle_low:.2f} | "
                f"Active={active}"
            )
            self.state_manager.set_last_event(msg)
            events.append(msg)
            return events

        sig_hi = state.signal_candle_high
        sig_lo = state.signal_candle_low

        if sig_hi is None or sig_lo is None:
            self.state_manager.set_last_closed_candle(candle_epoch)
            return events

        active = state.active

        if active == "SHORT_PUT":
            exit_due_to_signal_change = trend == "SELL"
            exit_due_to_breach = candle_close < float(sig_lo)
            reentry_condition = state.waiting_reentry and (candle_close > float(sig_hi))

            should_exit = False
            exit_reason = None

            if self.variant == 1:
                if exit_due_to_signal_change:
                    should_exit = True
                    exit_reason = "Supertrend changed to SELL"
            elif self.variant == 2:
                if exit_due_to_signal_change:
                    should_exit = True
                    exit_reason = "Supertrend changed to SELL"
            elif self.variant == 3:
                if exit_due_to_breach:
                    should_exit = True
                    exit_reason = f"close {candle_close:.2f} < signal low {float(sig_lo):.2f}"
                elif exit_due_to_signal_change:
                    should_exit = True
                    exit_reason = "Supertrend changed to SELL"
            elif self.variant == 4:
                if exit_due_to_breach:
                    should_exit = True
                    exit_reason = f"close {candle_close:.2f} < signal low {float(sig_lo):.2f}"

            if should_exit:
                self.state_manager.set_active(None)
                self.state_manager.set_waiting_reentry(True)
                msg = f"SHORT_PUT exit trigger: {exit_reason}"
                self.state_manager.set_last_event(msg)
                events.append(msg)
            elif reentry_condition:
                self.state_manager.set_active("SHORT_PUT")
                self.state_manager.set_waiting_reentry(False)
                msg = (
                    f"SHORT_PUT re-entry trigger: close {candle_close:.2f} "
                    f"> signal high {float(sig_hi):.2f}"
                )
                self.state_manager.set_last_event(msg)
                events.append(msg)

        elif active == "SHORT_CALL":
            exit_due_to_signal_change = trend == "BUY"
            exit_due_to_breach = candle_close > float(sig_hi)
            reentry_condition = state.waiting_reentry and (candle_close < float(sig_lo))

            should_exit = False
            exit_reason = None

            if self.variant == 1:
                if exit_due_to_signal_change:
                    should_exit = True
                    exit_reason = "Supertrend changed to BUY"
            elif self.variant == 2:
                if exit_due_to_signal_change:
                    should_exit = True
                    exit_reason = "Supertrend changed to BUY"
            elif self.variant == 3:
                if exit_due_to_breach:
                    should_exit = True
                    exit_reason = f"close {candle_close:.2f} > signal high {float(sig_hi):.2f}"
                elif exit_due_to_signal_change:
                    should_exit = True
                    exit_reason = "Supertrend changed to BUY"
            elif self.variant == 4:
                if exit_due_to_breach:
                    should_exit = True
                    exit_reason = f"close {candle_close:.2f} > signal high {float(sig_hi):.2f}"

            if should_exit:
                self.state_manager.set_active(None)
                self.state_manager.set_waiting_reentry(True)
                msg = f"SHORT_CALL exit trigger: {exit_reason}"
                self.state_manager.set_last_event(msg)
                events.append(msg)
            elif reentry_condition:
                self.state_manager.set_active("SHORT_CALL")
                self.state_manager.set_waiting_reentry(False)
                msg = (
                    f"SHORT_CALL re-entry trigger: close {candle_close:.2f} "
                    f"< signal low {float(sig_lo):.2f}"
                )
                self.state_manager.set_last_event(msg)
                events.append(msg)

        self.state_manager.set_last_closed_candle(candle_epoch)
        return events

    def snapshot(self) -> Dict[str, Any]:
        # Variant 5: delegate to dual_tf_engine snapshot
        if self.variant == 5 and self.dual_tf_engine is not None:
            snap = self.dual_tf_engine.snapshot()
            snap["variant"] = 5
            snap["sl_percent"] = self.sl_percent
            return snap

        state = self.state_manager.snapshot()
        return {
            "variant": self.variant,
            "sl_percent": self.sl_percent,
            "trend": self.current_trend,
            "signal": self.current_flip_signal,
            "supertrend": self.current_st_value,
            "atr": self.current_atr,
            "indicator_seeded": state.get("indicator_seeded"),
            "live_action_armed": state.get("live_action_armed"),
            "active": state.get("active"),
            "waiting_reentry": state.get("waiting_reentry"),
            "signal_candle_high": state.get("signal_candle_high"),
            "signal_candle_low": state.get("signal_candle_low"),
            "last_signal": state.get("last_signal"),
            "last_event": state.get("last_event"),
        }
