from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional


def _normalize_dhan_epoch(ts: int) -> int:
    ts = int(ts)
    now_ts = int(time.time())
    diff = ts - now_ts
    if int(4.5 * 3600) <= diff <= int(6.5 * 3600):
        ts -= 19800
    return ts


# Market open = 09:15 IST = 03:45 UTC = 13500 seconds from UTC midnight
_MARKET_OPEN_UTC_OFFSET = 13500   # 3h 45m in seconds
_DAY_SECONDS = 86400


def _market_open_epoch_for_day(epoch_sec: int) -> int:
    """Return the 09:15 IST epoch for the same calendar day as epoch_sec (UTC)."""
    day_start_utc = (epoch_sec // _DAY_SECONDS) * _DAY_SECONDS
    return day_start_utc + _MARKET_OPEN_UTC_OFFSET


class TimeframeCandleBuilder:
    def __init__(self, timeframe_minutes: int, history_size: int = 300) -> None:
        self.timeframe_minutes = int(timeframe_minutes)
        self.timeframe_seconds = self.timeframe_minutes * 60

        self.current: Optional[Dict[str, Any]] = None
        self.history: Deque[Dict[str, Any]] = deque(maxlen=int(history_size))

        self.last_ltp: Optional[float] = None
        self.last_ltt_epoch: Optional[int] = None

    def _bucket_epoch(self, epoch_sec: int) -> int:
        """
        Return the candle bucket start epoch aligned to 09:15 IST market open.

        For any timeframe, candles start at:
          09:15, 09:15+TF, 09:15+2*TF, ...

        Examples (60m): 09:15, 10:15, 11:15, 12:15, 13:15, 14:15
        Examples (30m): 09:15, 09:45, 10:15, 10:45, ...
        Examples ( 5m): 09:15, 09:20, 09:25, ...
        """
        epoch_sec = _normalize_dhan_epoch(int(epoch_sec))
        market_open = _market_open_epoch_for_day(epoch_sec)

        offset = epoch_sec - market_open

        if offset < 0:
            # Tick arrived before 09:15 (pre-market) — put in a pre-open bucket
            return market_open - self.timeframe_seconds

        # Floor offset to nearest timeframe boundary
        bucket_offset = (offset // self.timeframe_seconds) * self.timeframe_seconds
        return market_open + bucket_offset

    def _new_candle(self, bucket: int, price: float, ltt_epoch: int) -> Dict[str, Any]:
        return {
            "bucket": int(bucket),
            "epoch": int(bucket),
            "start_epoch": int(bucket),
            "timestamp": int(bucket),
            "time": int(bucket),
            "ts": int(bucket),
            "end_epoch": int(bucket + self.timeframe_seconds),
            "open": float(price),
            "high": float(price),
            "low": float(price),
            "close": float(price),
            "ticks": 1,
            "last_ltt_epoch": int(ltt_epoch),
        }

    def _append_closed(self, candle: Dict[str, Any]) -> None:
        self.history.appendleft(dict(candle))

    def seed_from_1m_history(self, candles_1m: List[Dict[str, Any]]) -> int:
        """
        Seed aggregated timeframe candles from 1-minute history.

        Closed historical candles go into self.history for Supertrend warmup.
        The LAST partial candle (current forming period) is kept as self.current
        so that live WS ticks continue it correctly — OHLC from history is preserved.
        """
        if not candles_1m:
            return 0

        temp_current: Optional[Dict[str, Any]] = None
        seeded_count = 0

        for cd in candles_1m:
            try:
                bucket_1m = _normalize_dhan_epoch(int(cd["bucket"]))
                o = float(cd["open"])
                h = float(cd["high"])
                l = float(cd["low"])
                c = float(cd["close"])
            except Exception:
                continue

            tf_bucket = self._bucket_epoch(bucket_1m)

            if temp_current is None:
                temp_current = {
                    "bucket": int(tf_bucket),
                    "epoch": int(tf_bucket),
                    "start_epoch": int(tf_bucket),
                    "timestamp": int(tf_bucket),
                    "time": int(tf_bucket),
                    "ts": int(tf_bucket),
                    "end_epoch": int(tf_bucket + self.timeframe_seconds),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "ticks": 1,
                    "last_ltt_epoch": int(bucket_1m),
                }
                continue

            if int(temp_current["bucket"]) == int(tf_bucket):
                temp_current["high"] = max(float(temp_current["high"]), h)
                temp_current["low"]  = min(float(temp_current["low"]), l)
                temp_current["close"] = c
                temp_current["ticks"] = int(temp_current.get("ticks", 0)) + 1
                temp_current["last_ltt_epoch"] = int(bucket_1m)
            else:
                self._append_closed(temp_current)
                seeded_count += 1

                temp_current = {
                    "bucket": int(tf_bucket),
                    "epoch": int(tf_bucket),
                    "start_epoch": int(tf_bucket),
                    "timestamp": int(tf_bucket),
                    "time": int(tf_bucket),
                    "ts": int(tf_bucket),
                    "end_epoch": int(tf_bucket + self.timeframe_seconds),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "ticks": 1,
                    "last_ltt_epoch": int(bucket_1m),
                }

        # ── Keep partial current candle (mid-session start fix) ───────────────
        # If the last 1m bar belongs to the still-forming timeframe bucket,
        # retain it as self.current so live WS ticks continue it correctly.
        # This preserves the Open and H/L from history when starting mid-candle.
        if temp_current is not None:
            import time as _time
            now_epoch = int(_time.time())
            candle_end = int(temp_current["end_epoch"])
            if now_epoch < candle_end:
                # Candle is still open — keep as current forming candle
                self.current = dict(temp_current)
            else:
                # Candle already closed (history goes right up to now) — add to history
                self._append_closed(temp_current)
                seeded_count += 1
                self.current = None
        else:
            self.current = None

        return seeded_count

    def on_tick(self, price: float, ltt_epoch: int) -> List[Dict[str, Any]]:
        price = float(price)
        ltt_epoch = _normalize_dhan_epoch(int(ltt_epoch))
        bucket = self._bucket_epoch(ltt_epoch)

        self.last_ltp = price
        self.last_ltt_epoch = ltt_epoch

        closed: List[Dict[str, Any]] = []

        if self.current is None:
            self.current = self._new_candle(bucket, price, ltt_epoch)
            return closed

        current_bucket = int(self.current["bucket"])

        if bucket == current_bucket:
            self.current["high"] = max(float(self.current["high"]), price)
            self.current["low"] = min(float(self.current["low"]), price)
            self.current["close"] = price
            self.current["ticks"] = int(self.current.get("ticks", 0)) + 1
            self.current["last_ltt_epoch"] = ltt_epoch
            return closed

        if bucket > current_bucket:
            finished = dict(self.current)
            finished["closed_at_epoch"] = ltt_epoch
            self._append_closed(finished)
            closed.append(finished)

            self.current = self._new_candle(bucket, price, ltt_epoch)
            return closed

        return closed

    def snapshot(self) -> Dict[str, Any]:
        return {
            "current": dict(self.current) if self.current else None,
            "history": list(self.history),
            "last_ltp": self.last_ltp,
            "last_ltt_epoch": self.last_ltt_epoch,
            "timeframe_minutes": self.timeframe_minutes,
        }