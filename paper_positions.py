from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import threading
from typing import Dict, List, Optional

from option_chain_engine import OptionSetup


@dataclass
class PaperPosition:
    status: str
    trend: str

    entry_time: str
    entry_spot: float

    expiry_text: str

    short_security_id: str
    short_symbol: str
    short_option_type: str
    short_strike: float
    short_entry_premium: float

    hedge_security_id: str
    hedge_symbol: str
    hedge_option_type: str
    hedge_strike: float
    hedge_entry_premium: float

    net_credit_entry: float
    otm_step: int
    hedge_distance_steps: int

    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None
    short_exit_premium: Optional[float] = None
    hedge_exit_premium: Optional[float] = None
    net_credit_exit: Optional[float] = None
    pnl_estimate: Optional[float] = None

    short_ltp: Optional[float] = None
    hedge_ltp: Optional[float] = None
    short_ltt_epoch: Optional[int] = None
    hedge_ltt_epoch: Optional[int] = None
    net_premium_now: Optional[float] = None
    pnl_points: Optional[float] = None
    pnl_rupees: Optional[float] = None
    max_profit_seen: Optional[float] = None
    max_loss_seen: Optional[float] = None
    short_sl_price: Optional[float] = None
    short_sl_hit: bool = False
    mtm_last_update: Optional[str] = None


@dataclass
class PaperBook:
    active_position: Optional[PaperPosition] = None
    history: List[PaperPosition] = field(default_factory=list)
    last_event: Optional[str] = None


class PaperPositionManager:
    def __init__(self, max_history: int = 100, lot_size: int = 75) -> None:
        self.book = PaperBook()
        self.max_history = int(max_history)
        self.lot_size = int(lot_size)
        self.lock = threading.RLock()
        self._sl_event_fired = False

    def has_active(self) -> bool:
        with self.lock:
            return self.book.active_position is not None and self.book.active_position.status == "OPEN"

    def open_position(self, setup: OptionSetup, spot_price: float, sl_percent: Optional[float] = None) -> str:
        with self.lock:
            if self.has_active():
                msg = "Paper position already active; skipped opening new setup"
                self.book.last_event = msg
                return msg

            now_txt = self._now_text()
            short_entry = float(setup.short_premium)
            hedge_entry = float(setup.hedge_premium)
            short_sl_price = None
            if sl_percent is not None:
                try:
                    short_sl_price = short_entry * (1.0 + float(sl_percent) / 100.0)
                except Exception:
                    short_sl_price = None

            pos = PaperPosition(
                status="OPEN",
                trend=str(setup.trend),
                entry_time=now_txt,
                entry_spot=float(spot_price),
                expiry_text=str(setup.expiry_text),
                short_security_id=str(setup.short_contract.security_id),
                short_symbol=str(setup.short_contract.trading_symbol),
                short_option_type=str(setup.short_contract.option_type),
                short_strike=float(setup.short_contract.strike),
                short_entry_premium=short_entry,
                hedge_security_id=str(setup.hedge_contract.security_id),
                hedge_symbol=str(setup.hedge_contract.trading_symbol),
                hedge_option_type=str(setup.hedge_contract.option_type),
                hedge_strike=float(setup.hedge_contract.strike),
                hedge_entry_premium=hedge_entry,
                net_credit_entry=float(setup.net_credit),
                otm_step=int(setup.otm_step),
                hedge_distance_steps=int(setup.hedge_distance_steps),
                short_ltp=short_entry,
                hedge_ltp=hedge_entry,
                net_premium_now=float(setup.net_credit),
                pnl_points=0.0,
                pnl_rupees=0.0,
                max_profit_seen=0.0,
                max_loss_seen=0.0,
                short_sl_price=short_sl_price,
                short_sl_hit=False,
                mtm_last_update=now_txt,
            )

            self.book.active_position = pos
            self._sl_event_fired = False
            msg = (
                f"Paper OPEN | trend={pos.trend} | short={pos.short_symbol} @ {pos.short_entry_premium:.2f} | "
                f"hedge={pos.hedge_symbol} @ {pos.hedge_entry_premium:.2f} | net={pos.net_credit_entry:.2f}"
            )
            self.book.last_event = msg
            return msg

    def close_active_position(self, reason: str) -> str:
        with self.lock:
            if not self.has_active():
                msg = "No active paper position to close"
                self.book.last_event = msg
                return msg

            pos = self.book.active_position
            assert pos is not None

            pos.status = "CLOSED"
            pos.exit_time = self._now_text()
            pos.exit_reason = str(reason)
            pos.short_exit_premium = pos.short_ltp
            pos.hedge_exit_premium = pos.hedge_ltp
            if pos.short_ltp is not None and pos.hedge_ltp is not None:
                pos.net_credit_exit = float(pos.short_ltp) - float(pos.hedge_ltp)
            pos.pnl_estimate = pos.pnl_points

            self.book.history.insert(0, pos)
            self.book.history = self.book.history[: self.max_history]
            self.book.active_position = None
            self._sl_event_fired = False

            msg = (
                f"Paper CLOSE | short={pos.short_symbol} | hedge={pos.hedge_symbol} | "
                f"reason={reason} | pnl_pts={self._fmt(pos.pnl_points)} | pnl_rs={self._fmt(pos.pnl_rupees)}"
            )
            self.book.last_event = msg
            return msg

    def active_option_watchlist(self) -> List[tuple[str, str]]:
        with self.lock:
            if not self.has_active():
                return []
            pos = self.book.active_position
            assert pos is not None
            return [
                ("NSE_FNO", pos.short_security_id),
                ("NSE_FNO", pos.hedge_security_id),
            ]

    def update_live_quote(self, security_id: str, ltp: float, ltt_epoch: int) -> Optional[str]:
        with self.lock:
            if not self.has_active():
                return None
            pos = self.book.active_position
            assert pos is not None

            sid = str(security_id).strip()
            changed = False
            if sid == pos.short_security_id:
                pos.short_ltp = float(ltp)
                pos.short_ltt_epoch = int(ltt_epoch)
                changed = True
            elif sid == pos.hedge_security_id:
                pos.hedge_ltp = float(ltp)
                pos.hedge_ltt_epoch = int(ltt_epoch)
                changed = True

            if not changed:
                return None

            if pos.short_ltp is not None and pos.hedge_ltp is not None:
                pos.net_premium_now = float(pos.short_ltp) - float(pos.hedge_ltp)
                pos.pnl_points = float(pos.net_credit_entry) - float(pos.net_premium_now)
                pos.pnl_rupees = float(pos.pnl_points) * float(self.lot_size)
                pos.max_profit_seen = max(float(pos.max_profit_seen or 0.0), float(pos.pnl_points))
                pos.max_loss_seen = min(float(pos.max_loss_seen or 0.0), float(pos.pnl_points))
                pos.mtm_last_update = self._now_text()

            sl_event = None
            if pos.short_sl_price is not None and pos.short_ltp is not None:
                if float(pos.short_ltp) >= float(pos.short_sl_price):
                    pos.short_sl_hit = True
                    if not self._sl_event_fired:
                        sl_event = (
                            f"Paper ALERT | short premium SL touched | short={pos.short_symbol} | "
                            f"ltp={float(pos.short_ltp):.2f} >= sl={float(pos.short_sl_price):.2f}"
                        )
                        self.book.last_event = sl_event
                        self._sl_event_fired = True
                else:
                    pos.short_sl_hit = False
            return sl_event

    def snapshot(self) -> Dict[str, object]:
        with self.lock:
            active = asdict(self.book.active_position) if self.book.active_position else None
            history = [asdict(x) for x in self.book.history[:10]]
            return {
                "has_active_position": self.has_active(),
                "active_position": active,
                "history": history,
                "last_event": self.book.last_event,
                "lot_size": self.lot_size,
            }

    def _now_text(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _fmt(self, x: Optional[float]) -> str:
        if x is None:
            return "-"
        try:
            return f"{float(x):.2f}"
        except Exception:
            return "-"
