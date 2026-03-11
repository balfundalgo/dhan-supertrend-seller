from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class RuntimeState:
    indicator_seeded: bool = False
    live_action_armed: bool = False

    timeframe_minutes: int = 1
    st_period: int = 10
    st_multiplier: float = 3.0

    last_closed_candle_epoch: Optional[int] = None
    last_signal: Optional[str] = None

    active: Optional[str] = None
    waiting_reentry: bool = False

    signal_candle_high: Optional[float] = None
    signal_candle_low: Optional[float] = None
    signal_candle_epoch: Optional[int] = None

    last_event: Optional[str] = None


class StateManager:
    def __init__(
        self,
        state_file: str | Path,
        timeframe_minutes: int,
        st_period: int,
        st_multiplier: float,
    ) -> None:
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        self.state = RuntimeState(
            timeframe_minutes=int(timeframe_minutes),
            st_period=int(st_period),
            st_multiplier=float(st_multiplier),
        )

        self._load()

        if (
            self.state.timeframe_minutes != int(timeframe_minutes)
            or self.state.st_period != int(st_period)
            or float(self.state.st_multiplier) != float(st_multiplier)
        ):
            self.reset_for_new_configuration(
                timeframe_minutes=int(timeframe_minutes),
                st_period=int(st_period),
                st_multiplier=float(st_multiplier),
            )

    def reset_for_new_configuration(
        self,
        timeframe_minutes: int,
        st_period: int,
        st_multiplier: float,
    ) -> None:
        self.state = RuntimeState(
            indicator_seeded=False,
            live_action_armed=False,
            timeframe_minutes=int(timeframe_minutes),
            st_period=int(st_period),
            st_multiplier=float(st_multiplier),
        )
        self.save()

    def _load(self) -> None:
        if not self.state_file.exists():
            return
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            current = asdict(self.state)
            for k in current.keys():
                if k in raw:
                    setattr(self.state, k, raw[k])
        except Exception:
            pass

    def save(self) -> None:
        self.state_file.write_text(
            json.dumps(asdict(self.state), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def mark_indicator_seeded(self) -> None:
        self.state.indicator_seeded = True
        self.save()

    def arm_live_actions(self) -> None:
        self.state.live_action_armed = True
        self.save()

    def set_last_closed_candle(self, candle_epoch: int) -> None:
        self.state.last_closed_candle_epoch = int(candle_epoch)
        self.save()

    def set_signal(
        self,
        signal: str,
        candle_high: float,
        candle_low: float,
        candle_epoch: int,
        active: Optional[str] = None,
    ) -> None:
        self.state.last_signal = signal
        self.state.signal_candle_high = float(candle_high)
        self.state.signal_candle_low = float(candle_low)
        self.state.signal_candle_epoch = int(candle_epoch)
        if active is not None:
            self.state.active = active
        self.state.waiting_reentry = False
        self.save()

    def seed_trend_context(
        self,
        trend: str,
        active: Optional[str] = None,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None,
        candle_epoch: Optional[int] = None,
    ) -> None:
        self.state.last_signal = str(trend)
        if active is not None:
            self.state.active = active
        self.state.waiting_reentry = False
        self.state.signal_candle_high = float(candle_high) if candle_high is not None else None
        self.state.signal_candle_low = float(candle_low) if candle_low is not None else None
        self.state.signal_candle_epoch = int(candle_epoch) if candle_epoch is not None else None
        self.save()

    def clear_signal_context(self) -> None:
        self.state.last_signal = None
        self.state.signal_candle_high = None
        self.state.signal_candle_low = None
        self.state.signal_candle_epoch = None
        self.save()

    def clear_lifecycle(self, keep_last_event: bool = True) -> None:
        last_event = self.state.last_event if keep_last_event else None
        self.state.active = None
        self.state.waiting_reentry = False
        self.state.last_signal = None
        self.state.signal_candle_high = None
        self.state.signal_candle_low = None
        self.state.signal_candle_epoch = None
        self.state.last_event = last_event
        self.save()

    def set_active(self, active: Optional[str]) -> None:
        self.state.active = active
        self.save()

    def set_waiting_reentry(self, waiting: bool) -> None:
        self.state.waiting_reentry = bool(waiting)
        self.save()

    def set_last_event(self, text: str) -> None:
        self.state.last_event = str(text)
        self.save()

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self.state)
