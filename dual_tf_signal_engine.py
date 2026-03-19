"""
dual_tf_signal_engine.py
========================
Variant 5 — Dual Timeframe Trailing Strategy

Higher TF (HTF): determines direction only (e.g. 60m)
Lower  TF (LTF): controls entry, exit, re-entry timing (e.g. 15m)

Entry rules:
  HTF=BUY  + LTF=BUY  → enter SHORT_PUT  immediately
  HTF=BUY  + LTF=SELL → wait for LTF to flip BUY
  HTF=SELL + LTF=SELL → enter SHORT_CALL immediately
  HTF=SELL + LTF=BUY  → wait for LTF to flip SELL

Exit rules (LTF driven):
  In SHORT_PUT:  exit when LTF flips SELL  OR HTF flips SELL
  In SHORT_CALL: exit when LTF flips BUY   OR HTF flips BUY
  Exit strictly on LTF candle close.

Re-entry rules:
  After exit, wait for LTF signal to agree with HTF again.
  If HTF has flipped → no re-entry in old direction.

All entries/exits are on candle close only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _get_candle_epoch(candle: Dict[str, Any]) -> int:
    for key in ("bucket", "epoch", "start_epoch", "ts", "timestamp", "time"):
        if key in candle and candle[key] is not None:
            return int(candle[key])
    raise KeyError("No candle epoch field found.")


class DualTFSignalEngine:
    """
    Manages two independent Supertrend streams (HTF + LTF) and
    implements the Variant 5 dual-timeframe entry/exit/re-entry logic.

    The engine is stateless between restarts (no file persistence) — it
    re-seeds from history on startup exactly like the single-TF engine.
    """

    def __init__(self, sl_percent: float) -> None:
        self.sl_percent = float(sl_percent)

        # ── HTF state ────────────────────────────────────────────────────────
        self.htf_trend:  Optional[str] = None   # BUY / SELL / None
        self.htf_signal: Optional[str] = None   # flip signal on this candle
        self.htf_st:     Optional[float] = None
        self.htf_atr:    Optional[float] = None
        self.htf_seeded: bool = False
        self.htf_armed:  bool = False

        # ── LTF state ────────────────────────────────────────────────────────
        self.ltf_trend:  Optional[str] = None
        self.ltf_signal: Optional[str] = None
        self.ltf_st:     Optional[float] = None
        self.ltf_atr:    Optional[float] = None
        self.ltf_seeded: bool = False
        self.ltf_armed:  bool = False

        # ── Position / lifecycle state ───────────────────────────────────────
        self.active:          Optional[str] = None  # SHORT_PUT / SHORT_CALL / None
        self.waiting_reentry: bool = False

        # Signal candle H/L tracked on LTF (used for exit display)
        self.ltf_signal_candle_high: Optional[float] = None
        self.ltf_signal_candle_low:  Optional[float] = None
        self.ltf_signal_candle_epoch: Optional[int] = None

        self.last_event: Optional[str] = None

    # ── History warmup ────────────────────────────────────────────────────────

    def process_htf_historical(self, candle: Dict[str, Any], st_result: Dict[str, Any]) -> None:
        """Feed HTF candles from history (no trade actions)."""
        self.htf_trend  = st_result.get("trend")
        self.htf_signal = st_result.get("signal")
        self.htf_st     = st_result.get("supertrend")
        self.htf_atr    = st_result.get("atr")

    def process_ltf_historical(self, candle: Dict[str, Any], st_result: Dict[str, Any]) -> None:
        """Feed LTF candles from history (no trade actions)."""
        self.ltf_trend  = st_result.get("trend")
        self.ltf_signal = st_result.get("signal")
        self.ltf_st     = st_result.get("supertrend")
        self.ltf_atr    = st_result.get("atr")

    def mark_htf_seeded(self) -> None:
        self.htf_seeded = True

    def mark_ltf_seeded(self) -> None:
        self.ltf_seeded = True

    # ── Live candle processing ────────────────────────────────────────────────

    def process_htf_live(self, candle: Dict[str, Any], st_result: Dict[str, Any]) -> List[str]:
        """
        Called when a new HTF candle closes.
        HTF flip always exits current position regardless of LTF.
        """
        events: List[str] = []

        prev_trend = self.htf_trend
        self.htf_trend  = st_result.get("trend")
        self.htf_signal = st_result.get("signal")
        self.htf_st     = st_result.get("supertrend")
        self.htf_atr    = st_result.get("atr")

        if not self.htf_armed:
            self.htf_armed = True
            events.append(f"HTF armed | trend={self.htf_trend}")
            return events

        flip = self.htf_signal

        # HTF flip → exit position immediately
        if flip in ("BUY", "SELL") and flip != prev_trend:
            if self.active is not None:
                msg = (
                    f"HTF flip to {flip} → exit {self.active} | "
                    f"HTF ST={self.htf_st:.2f}"
                )
                self.active = None
                self.waiting_reentry = False
                self.last_event = msg
                events.append(msg)
            else:
                events.append(f"HTF flip to {flip} (no active position)")

        return events

    def process_ltf_live(self, candle: Dict[str, Any], st_result: Dict[str, Any]) -> List[str]:
        """
        Called when a new LTF candle closes.
        This is where all entries, LTF-driven exits, and re-entries happen.
        """
        events: List[str] = []

        candle_epoch = _get_candle_epoch(candle)
        candle_high  = float(candle["high"])
        candle_low   = float(candle["low"])

        prev_ltf_trend = self.ltf_trend
        self.ltf_trend  = st_result.get("trend")
        self.ltf_signal = st_result.get("signal")
        self.ltf_st     = st_result.get("supertrend")
        self.ltf_atr    = st_result.get("atr")

        if not self.ltf_armed:
            self.ltf_armed = True
            events.append(f"LTF armed | trend={self.ltf_trend}")
            return events

        htf = self.htf_trend
        ltf = self.ltf_trend
        ltf_flip = self.ltf_signal

        # ── Case 1: LTF flip occurred on this candle ─────────────────────────
        if ltf_flip in ("BUY", "SELL"):

            # Track LTF signal candle H/L
            self.ltf_signal_candle_high  = candle_high
            self.ltf_signal_candle_low   = candle_low
            self.ltf_signal_candle_epoch = candle_epoch

            # ── Exit check ────────────────────────────────────────────────────
            if self.active == "SHORT_PUT" and ltf_flip == "SELL":
                msg = (
                    f"LTF flip SELL → exit SHORT_PUT | "
                    f"LTF candle H={candle_high:.2f} L={candle_low:.2f}"
                )
                self.active = None
                self.waiting_reentry = True
                self.last_event = msg
                events.append(msg)
                # After exit, immediately check re-entry in new direction
                # (HTF=BUY means we want to re-enter PUT on next BUY flip)

            elif self.active == "SHORT_CALL" and ltf_flip == "BUY":
                msg = (
                    f"LTF flip BUY → exit SHORT_CALL | "
                    f"LTF candle H={candle_high:.2f} L={candle_low:.2f}"
                )
                self.active = None
                self.waiting_reentry = True
                self.last_event = msg
                events.append(msg)

            # ── Entry / Re-entry check ────────────────────────────────────────
            if self.active is None and htf in ("BUY", "SELL"):

                # LTF agrees with HTF → enter
                if htf == "BUY" and ltf_flip == "BUY":
                    self.active = "SHORT_PUT"
                    self.waiting_reentry = False
                    msg = (
                        f"HTF=BUY + LTF flip BUY → {'Re-entry' if self.waiting_reentry else 'Entry'} SHORT_PUT | "
                        f"LTF candle H={candle_high:.2f} L={candle_low:.2f}"
                    )
                    self.last_event = msg
                    events.append(msg)

                elif htf == "SELL" and ltf_flip == "SELL":
                    self.active = "SHORT_CALL"
                    self.waiting_reentry = False
                    msg = (
                        f"HTF=SELL + LTF flip SELL → {'Re-entry' if self.waiting_reentry else 'Entry'} SHORT_CALL | "
                        f"LTF candle H={candle_high:.2f} L={candle_low:.2f}"
                    )
                    self.last_event = msg
                    events.append(msg)

                elif htf == "BUY" and ltf_flip == "SELL":
                    msg = "HTF=BUY but LTF=SELL — waiting for LTF BUY flip"
                    self.last_event = msg
                    events.append(msg)

                elif htf == "SELL" and ltf_flip == "BUY":
                    msg = "HTF=SELL but LTF=BUY — waiting for LTF SELL flip"
                    self.last_event = msg
                    events.append(msg)

        return events

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        return {
            # HTF
            "htf_trend":   self.htf_trend,
            "htf_signal":  self.htf_signal,
            "htf_st":      self.htf_st,
            "htf_atr":     self.htf_atr,
            "htf_seeded":  self.htf_seeded,
            "htf_armed":   self.htf_armed,
            # LTF
            "ltf_trend":   self.ltf_trend,
            "ltf_signal":  self.ltf_signal,
            "ltf_st":      self.ltf_st,
            "ltf_atr":     self.ltf_atr,
            "ltf_seeded":  self.ltf_seeded,
            "ltf_armed":   self.ltf_armed,
            # position
            "active":           self.active,
            "waiting_reentry":  self.waiting_reentry,
            "ltf_signal_candle_high":  self.ltf_signal_candle_high,
            "ltf_signal_candle_low":   self.ltf_signal_candle_low,
            "last_event":  self.last_event,
            # compatibility keys used by dashboard / paper manager
            "trend":              self.htf_trend,
            "signal":             self.ltf_signal,
            "indicator_seeded":   self.htf_seeded and self.ltf_seeded,
            "live_action_armed":  self.htf_armed and self.ltf_armed,
            "signal_candle_high": self.ltf_signal_candle_high,
            "signal_candle_low":  self.ltf_signal_candle_low,
            "last_signal":        self.ltf_signal,
        }
