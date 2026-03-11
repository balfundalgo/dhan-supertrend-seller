from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, Optional


class SupertrendEngine:
    def __init__(self, atr_period: int, multiplier: float) -> None:
        self.atr_period = int(atr_period)
        self.multiplier = float(multiplier)

        self.prev_close: Optional[float] = None
        self.tr_queue: Deque[float] = deque(maxlen=max(1, self.atr_period))

        self.atr: Optional[float] = None
        self.fub: Optional[float] = None
        self.flb: Optional[float] = None
        self.value: Optional[float] = None

        self.direction: Optional[int] = None   # +1 uptrend, -1 downtrend
        self.current_trend: Optional[str] = None   # BUY / SELL persistent state
        self.flip_signal: Optional[str] = None     # BUY / SELL only on actual flip

        self.bar_count: int = 0
        self.last_bucket: Optional[int] = None

    def reset(self) -> None:
        self.__init__(self.atr_period, self.multiplier)

    def _true_range(self, high: float, low: float, prev_close: Optional[float]) -> float:
        if prev_close is None:
            return float(high) - float(low)
        pc = float(prev_close)
        return max(
            float(high) - float(low),
            abs(float(high) - pc),
            abs(float(low) - pc),
        )

    def update(self, candle: Dict[str, Any]) -> Dict[str, Any]:
        h = float(candle["high"])
        l = float(candle["low"])
        c = float(candle["close"])
        bucket = int(candle.get("bucket", candle.get("epoch", 0)))

        self.last_bucket = bucket
        self.bar_count += 1

        tr = self._true_range(h, l, self.prev_close)
        self.tr_queue.append(float(tr))

        n = self.atr_period

        # ATR seed with SMA of first n TR values
        if self.atr is None:
            if len(self.tr_queue) < n:
                self.prev_close = float(c)
                self.flip_signal = None
                self.current_trend = None
                return self.snapshot()
            self.atr = sum(self.tr_queue) / float(n)
        else:
            # Wilder / RMA smoothing
            alpha = 1.0 / float(n)
            self.atr = alpha * float(tr) + (1.0 - alpha) * float(self.atr)

        atr = float(self.atr)
        hl2 = (float(h) + float(l)) / 2.0

        basic_upper = hl2 + self.multiplier * atr
        basic_lower = hl2 - self.multiplier * atr

        prev_upper = basic_upper if self.fub is None else float(self.fub)
        prev_lower = basic_lower if self.flb is None else float(self.flb)
        prev_close = float(c) if self.prev_close is None else float(self.prev_close)

        upper = basic_upper if (basic_upper < prev_upper or prev_close > prev_upper) else prev_upper
        lower = basic_lower if (basic_lower > prev_lower or prev_close < prev_lower) else prev_lower

        flip_signal = None

        # Match your working file logic exactly
        if self.direction is None:
            direction = 1
            st_val = lower
        else:
            if int(self.direction) == 1:
                if float(c) < lower:
                    direction = -1
                    st_val = upper
                    flip_signal = "SELL"
                else:
                    direction = 1
                    st_val = lower
            else:
                if float(c) > upper:
                    direction = 1
                    st_val = lower
                    flip_signal = "BUY"
                else:
                    direction = -1
                    st_val = upper

        self.fub = float(upper)
        self.flb = float(lower)
        self.value = float(st_val)
        self.direction = int(direction)
        self.flip_signal = flip_signal
        self.current_trend = "BUY" if self.direction == 1 else "SELL"
        self.prev_close = float(c)

        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "bucket": self.last_bucket,
            "atr": self.atr,
            "supertrend": self.value,
            "direction": self.direction,
            "trend": self.current_trend,      # persistent trend
            "signal": self.flip_signal,       # only on actual flip
            "fub": self.fub,
            "flb": self.flb,
            "bar_count": self.bar_count,
            "atr_period": self.atr_period,
            "multiplier": self.multiplier,
        }