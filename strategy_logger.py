"""
strategy_logger.py
==================
Logging for Balfund Supertrend Option Seller.

Two log files, both date-based (one per calendar day):

  logs/activity/strategy_YYYYMMDD.log   — full activity log, appends across sessions
  logs/trades/trades_YYYYMMDD.csv       — one row per completed trade

Usage:
    from strategy_logger import (
        log, log_section, log_config, log_candle,
        log_signal_state, log_option_snap, log_paper_snap,
        log_tick, get_log_path, get_trade_log_path,
        log_trade_open, log_trade_close,
    )
"""
from __future__ import annotations

import csv
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

# ── Resolve base dir (works both as .py and frozen .exe) ─────────────────────
if getattr(sys, 'frozen', False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent

_ACTIVITY_DIR = _BASE / "logs" / "activity"
_TRADE_DIR    = _BASE / "logs" / "trades"
_ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
_TRADE_DIR.mkdir(parents=True, exist_ok=True)

_lock       = threading.Lock()
_trade_lock = threading.Lock()


# ── Date helpers ──────────────────────────────────────────────────────────────
def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")

def _activity_file() -> Path:
    return _ACTIVITY_DIR / f"strategy_{_today_str()}.log"

def _trade_file() -> Path:
    return _TRADE_DIR / f"trades_{_today_str()}.csv"


# ── Daily file handler — appends to today's log, rolls on date change ─────────
class _DailyFileHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self._current_date: str | None = None
        self._stream = None
        self._fmt = logging.Formatter(
            "%(asctime)s.%(msecs)03d  [%(levelname)-5s]  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

    def _open_for_today(self):
        today = _today_str()
        if self._current_date == today and self._stream is not None:
            return
        # Close previous stream
        if self._stream:
            try:
                self._stream.close()
            except Exception:
                pass
        path = _activity_file()
        self._stream = open(path, "a", encoding="utf-8", buffering=1)
        self._current_date = today
        # Session start marker
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._stream.write(f"\n{'='*70}\n")
        self._stream.write(f"  SESSION START: {ts}\n")
        self._stream.write(f"{'='*70}\n\n")
        self._stream.flush()

    def emit(self, record: logging.LogRecord):
        try:
            self._open_for_today()
            self._stream.write(self._fmt.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        if self._stream:
            try:
                self._stream.close()
            except Exception:
                pass
        super().close()


# ── Logger setup ──────────────────────────────────────────────────────────────
_logger = logging.getLogger("BalfundStrategy")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
_logger.handlers.clear()

_daily_handler = _DailyFileHandler()
_daily_handler.setLevel(logging.DEBUG)
_logger.addHandler(_daily_handler)

_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
_logger.addHandler(_ch)


# ── Trade CSV ─────────────────────────────────────────────────────────────────
_TRADE_HEADERS = [
    "date", "trade_no",
    "entry_time", "exit_time",
    "trend", "expiry",
    "short_symbol", "short_entry", "short_exit",
    "hedge_symbol", "hedge_entry", "hedge_exit",
    "net_credit_entry", "net_premium_exit",
    "pnl_points", "pnl_rupees",
    "lot_size", "exit_reason",
]

_trade_counter: dict = {}
_open_trade: dict = {}


def _next_trade_no(date_key: str) -> int:
    if date_key not in _trade_counter:
        path = _TRADE_DIR / f"trades_{date_key}.csv"
        count = 0
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    count = max(sum(1 for _ in csv.reader(f)) - 1, 0)
            except Exception:
                count = 0
        _trade_counter[date_key] = count
    _trade_counter[date_key] += 1
    return _trade_counter[date_key]


def _ensure_csv_header(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(_TRADE_HEADERS)


# ── Public API ────────────────────────────────────────────────────────────────

def get_log_path() -> str:
    return str(_activity_file().resolve())

def get_trade_log_path() -> str:
    return str(_trade_file().resolve())


def log(msg: str, level: str = "INFO") -> None:
    with _lock:
        _logger.log(getattr(logging, level.upper(), logging.INFO), msg)

def log_debug(msg: str) -> None:
    log(msg, "DEBUG")

def log_warn(msg: str) -> None:
    log(msg, "WARNING")

def log_error(msg: str) -> None:
    log(msg, "ERROR")


def log_section(title: str) -> None:
    sep = "─" * 70
    with _lock:
        _logger.info("")
        _logger.info(sep)
        _logger.info("  %s", title)
        _logger.info(sep)


def log_config(cfg: dict) -> None:
    log_section("STRATEGY CONFIGURATION")
    for k, v in sorted(cfg.items()):
        log(f"  {k:<30} = {v}")


def log_candle(prefix: str, candle: dict, st_result: dict) -> None:
    try:
        from datetime import datetime as dt
        epoch = candle.get("bucket") or candle.get("epoch") or candle.get("time") or 0
        t = dt.fromtimestamp(int(epoch)).strftime("%H:%M") if epoch else "--:--"
        o, h, l, c = (float(candle.get(k, 0)) for k in ("open","high","low","close"))
        trend  = st_result.get("trend")  or "-"
        signal = st_result.get("signal") or "-"
        sv = st_result.get("supertrend")
        av = st_result.get("atr")
        st_str  = f"{sv:.2f}" if sv is not None else "-"
        atr_str = f"{av:.2f}" if av is not None else "-"
        log(
            f"{prefix} | {t} | O={o:.2f} H={h:.2f} L={l:.2f} C={c:.2f} | "
            f"Trend={trend} Signal={signal} ST={st_str} ATR={atr_str}"
        )
    except Exception as e:
        log(f"{prefix} | candle log error: {e}", "DEBUG")


def log_signal_state(snap: dict) -> None:
    log(
        f"SIGNAL STATE | variant={snap.get('variant')} | "
        f"trend={snap.get('trend')} | signal={snap.get('signal')} | "
        f"active={snap.get('active')} | waiting_reentry={snap.get('waiting_reentry')} | "
        f"seeded={snap.get('indicator_seeded')} | armed={snap.get('live_action_armed')} | "
        f"sig_hi={snap.get('signal_candle_high')} | sig_lo={snap.get('signal_candle_low')}"
    )


def log_option_snap(snap: dict) -> None:
    log(
        f"OPTION STATE | has_setup={snap.get('has_setup')} | "
        f"expiry={snap.get('expiry_current')} | trend={snap.get('trend')} | "
        f"short={snap.get('short_symbol')} @ {snap.get('short_premium')} | "
        f"hedge={snap.get('hedge_symbol')} @ {snap.get('hedge_premium')} | "
        f"net={snap.get('net_credit')}"
    )
    if snap.get("last_reason"):
        log(f"OPTION REASON | {snap.get('last_reason')}", "DEBUG")


def log_paper_snap(snap: dict) -> None:
    pos = snap.get("active_position") or {}
    log(
        f"PAPER STATE | has_active={snap.get('has_active_position')} | "
        f"trend={pos.get('trend')} | short={pos.get('short_symbol')} | "
        f"entry_premium={pos.get('short_entry_premium')} | "
        f"sl_price={pos.get('short_sl_price')} | sl_hit={pos.get('short_sl_hit')} | "
        f"pnl_pts={pos.get('pnl_points')} | pnl_rs={pos.get('pnl_rupees')}"
    )


def log_tick(price: float, ltt_epoch: int, interval: int = 100) -> None:
    if not hasattr(log_tick, "_count"):
        log_tick._count = 0
    log_tick._count += 1
    if log_tick._count % interval == 0:
        from datetime import datetime as dt
        t = dt.fromtimestamp(int(ltt_epoch)).strftime("%H:%M:%S")
        log_debug(f"TICK #{log_tick._count} | price={price:.2f} | ltt={t}")


# ── Trade logging ─────────────────────────────────────────────────────────────

def log_trade_open(position_snap: dict) -> None:
    """Call immediately after paper position opens. Stores entry for later CSV write."""
    global _open_trade
    pos = position_snap.get("active_position") or position_snap

    entry = {
        "date":             datetime.now().strftime("%Y-%m-%d"),
        "entry_time":       datetime.now().strftime("%H:%M:%S"),
        "trend":            pos.get("trend", ""),
        "expiry":           pos.get("expiry_text", "") or pos.get("short_expiry", ""),
        "short_symbol":     pos.get("short_symbol", ""),
        "short_entry":      pos.get("short_entry_premium", ""),
        "hedge_symbol":     pos.get("hedge_symbol", ""),
        "hedge_entry":      pos.get("hedge_entry_premium", ""),
        "net_credit_entry": pos.get("net_credit_entry", ""),
        "lot_size":         pos.get("lot_size", 75),
    }
    with _trade_lock:
        _open_trade = entry

    log_section("TRADE OPEN")
    log(
        f"TRADE OPEN | {entry['entry_time']} | trend={entry['trend']} | "
        f"expiry={entry['expiry']} | "
        f"short={entry['short_symbol']} @ {entry['short_entry']} | "
        f"hedge={entry['hedge_symbol']} @ {entry['hedge_entry']} | "
        f"net={entry['net_credit_entry']} | lot={entry['lot_size']}"
    )


def log_trade_close(position_snap: dict, exit_reason: str = "") -> None:
    """Call immediately after paper position closes. Writes row to today's CSV."""
    global _open_trade
    pos = position_snap.get("active_position") or position_snap

    exit_time = datetime.now().strftime("%H:%M:%S")
    date_key  = _today_str()
    date_str  = datetime.now().strftime("%Y-%m-%d")

    with _trade_lock:
        entry = dict(_open_trade)

    short_exit = pos.get("short_ltp") or pos.get("short_exit_premium", "")
    hedge_exit = pos.get("hedge_ltp") or pos.get("hedge_exit_premium", "")
    pnl_pts    = pos.get("pnl_points", "")
    pnl_rs     = pos.get("pnl_rupees", "")
    lot_size   = entry.get("lot_size") or pos.get("lot_size", 75)

    try:
        net_exit = f"{float(short_exit) - float(hedge_exit):.2f}"
    except Exception:
        net_exit = ""

    log_section("TRADE CLOSE")
    log(
        f"TRADE CLOSE | {exit_time} | reason={exit_reason} | "
        f"short_exit={short_exit} | hedge_exit={hedge_exit} | "
        f"net_exit={net_exit} | pnl_pts={pnl_pts} | pnl_rs={pnl_rs}"
    )

    csv_path = _TRADE_DIR / f"trades_{date_key}.csv"
    with _trade_lock:
        trade_no = _next_trade_no(date_key)
        _ensure_csv_header(csv_path)
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                date_str, trade_no,
                entry.get("entry_time", ""), exit_time,
                entry.get("trend", ""),
                entry.get("expiry", ""),
                entry.get("short_symbol", ""), entry.get("short_entry", ""), short_exit,
                entry.get("hedge_symbol", ""), entry.get("hedge_entry", ""), hedge_exit,
                entry.get("net_credit_entry", ""), net_exit,
                pnl_pts, pnl_rs,
                lot_size,
                exit_reason,
            ])
        _open_trade = {}

    log(f"Trade #{trade_no} saved → {csv_path.name}")
    log(f"Trade log: {str(csv_path.resolve())}")
