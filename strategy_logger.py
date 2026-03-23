"""
strategy_logger.py
==================
Persistent file logger for Balfund Supertrend Option Seller.

Creates a timestamped log file next to the .exe (or script) every session.
Log file path: <exe_folder>/logs/strategy_YYYYMMDD_HHMMSS.log

Usage:
    from strategy_logger import log, log_section, get_log_path
    log("Tick received: 23950.50")
    log_section("BOOTSTRAP")
"""
from __future__ import annotations

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

_LOG_DIR = _BASE / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_SESSION_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
_LOG_FILE   = _LOG_DIR / f"strategy_{_SESSION_TS}.log"

# ── Logger setup ──────────────────────────────────────────────────────────────
_logger = logging.getLogger("BalfundStrategy")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False

# File handler — DEBUG and above
_fh = logging.FileHandler(_LOG_FILE, encoding="utf-8", delay=False)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter(
    "%(asctime)s.%(msecs)03d  [%(levelname)-5s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
_logger.addHandler(_fh)

# Console handler — INFO and above
_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
_logger.addHandler(_ch)

_lock = threading.Lock()


def get_log_path() -> str:
    """Return absolute path of the current session log file."""
    return str(_LOG_FILE.resolve())


def log(msg: str, level: str = "INFO") -> None:
    """Write a line to the log file."""
    with _lock:
        lvl = getattr(logging, level.upper(), logging.INFO)
        _logger.log(lvl, msg)


def log_debug(msg: str) -> None:
    log(msg, "DEBUG")


def log_warn(msg: str) -> None:
    log(msg, "WARNING")


def log_error(msg: str) -> None:
    log(msg, "ERROR")


def log_section(title: str) -> None:
    """Write a visible section separator."""
    sep = "─" * 70
    with _lock:
        _logger.info("")
        _logger.info(sep)
        _logger.info("  %s", title)
        _logger.info(sep)


def log_config(cfg: dict) -> None:
    """Log all strategy configuration values."""
    log_section("STRATEGY CONFIGURATION")
    for k, v in sorted(cfg.items()):
        log(f"  {k:<30} = {v}")


def log_candle(prefix: str, candle: dict, st_result: dict) -> None:
    """Log a candle with its ST result."""
    try:
        from datetime import datetime as dt
        epoch = candle.get("bucket") or candle.get("epoch") or candle.get("time") or 0
        t = dt.fromtimestamp(int(epoch)).strftime("%H:%M") if epoch else "--:--"
        o = float(candle.get("open", 0))
        h = float(candle.get("high", 0))
        l = float(candle.get("low", 0))
        c = float(candle.get("close", 0))
        trend  = st_result.get("trend")  or "-"
        signal = st_result.get("signal") or "-"
        st_val = st_result.get("supertrend")
        atr    = st_result.get("atr")
        log(
            f"{prefix} | {t} | O={o:.2f} H={h:.2f} L={l:.2f} C={c:.2f} | "
            f"Trend={trend} Signal={signal} ST={st_val:.2f if st_val else '-'} ATR={atr:.2f if atr else '-'}"
        )
    except Exception as e:
        log(f"{prefix} | candle log error: {e}", "DEBUG")


def log_signal_state(snap: dict) -> None:
    """Log full signal engine snapshot."""
    log(
        f"SIGNAL STATE | variant={snap.get('variant')} | "
        f"trend={snap.get('trend')} | signal={snap.get('signal')} | "
        f"active={snap.get('active')} | waiting_reentry={snap.get('waiting_reentry')} | "
        f"seeded={snap.get('indicator_seeded')} | armed={snap.get('live_action_armed')} | "
        f"sig_hi={snap.get('signal_candle_high')} | sig_lo={snap.get('signal_candle_low')}"
    )


def log_option_snap(snap: dict) -> None:
    """Log option bridge snapshot."""
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
    """Log paper position snapshot."""
    pos = snap.get("active_position") or {}
    log(
        f"PAPER STATE | has_active={snap.get('has_active_position')} | "
        f"trend={pos.get('trend')} | short={pos.get('short_symbol')} | "
        f"entry_premium={pos.get('short_entry_premium')} | "
        f"sl_price={pos.get('short_sl_price')} | sl_hit={pos.get('short_sl_hit')} | "
        f"pnl_pts={pos.get('pnl_points')} | pnl_rs={pos.get('pnl_rupees')}"
    )


def log_tick(price: float, ltt_epoch: int, interval: int = 50) -> None:
    """Log tick — only every `interval` ticks to avoid flooding."""
    if not hasattr(log_tick, "_count"):
        log_tick._count = 0
    log_tick._count += 1
    if log_tick._count % interval == 0:
        from datetime import datetime as dt
        t = dt.fromtimestamp(int(ltt_epoch)).strftime("%H:%M:%S")
        log_debug(f"TICK #{log_tick._count} | price={price:.2f} | ltt={t}")
