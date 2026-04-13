"""
strategy_logger.py
==================
Logging for Balfund Supertrend Option Seller.
Matches DhanHA Trader logging style.

Folder structure (next to the .exe):
  logs/YYYY-MM-DD.log     — full activity log, rolls at midnight, keeps 30 days
  trades/YYYY-MM-DD.csv   — one row per trade event (OPEN / CLOSE)

Log format:
  2026-04-13 09:15:05,123 [INFO ] TRADE OPEN | ...

Usage:
    from strategy_logger import (
        setup_logger, get_logger,
        get_log_path, get_trade_log_path,
        log_trade_open, log_trade_close,
        log_section, log_config, log_candle,
        log_signal_state, log_option_snap, log_paper_snap, log_tick,
        log, log_debug, log_warn, log_error,
    )
    setup_logger()   # call once at strategy start
"""
from __future__ import annotations

import csv
import logging
import logging.handlers
import sys
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

# ── Resolve base dir (works as .py and as frozen .exe) ───────────────────────
if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent

_LOGS_DIR   = _BASE / "logs"
_TRADES_DIR = _BASE / "trades"
_LOGS_DIR.mkdir(exist_ok=True)
_TRADES_DIR.mkdir(exist_ok=True)

# ── Module-level logger (initialised by setup_logger()) ──────────────────────
_logger: Optional[logging.Logger] = None
_lock       = threading.Lock()
_trade_lock = threading.Lock()

# ── In-memory trade entry buffer (holds open trade details until close) ───────
_open_trade: dict = {}
_trade_counter: dict = {}   # date_str → int


# ── CSV headers (matches HA Trader + entry_time / exit_time added) ────────────
_TRADE_HEADERS = [
    "date", "trade_no",
    "event_type",          # OPEN / CLOSE
    "symbol",
    "trend",               # BUY / SELL
    "expiry",
    "entry_time",          # HH:MM:SS when position opened
    "exit_time",           # HH:MM:SS when position closed  (blank on OPEN row)
    "short_symbol",
    "short_entry",
    "short_exit",          # blank on OPEN row
    "hedge_symbol",
    "hedge_entry",
    "hedge_exit",          # blank on OPEN row
    "net_credit_entry",
    "net_premium_exit",    # blank on OPEN row
    "pnl_points",          # blank on OPEN row
    "pnl_rupees",          # blank on OPEN row
    "lot_size",
    "exit_reason",         # blank on OPEN row
]


# ─────────────────────────────────────────────────────────────────────────────
# Public setup
# ─────────────────────────────────────────────────────────────────────────────

def setup_logger() -> logging.Logger:
    """
    Initialise the module-level logger.
    Call once at strategy start.
    Uses TimedRotatingFileHandler — rolls at local midnight, keeps 30 days.
    """
    global _logger

    today     = date.today().strftime("%Y-%m-%d")
    log_path  = _LOGS_DIR / f"{today}.log"

    logger = logging.getLogger(f"BalfundST_{id(log_path)}")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    # File handler — rolls at midnight, keeps 30 days
    fh = logging.handlers.TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    fh.suffix = "%Y-%m-%d"
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    # Console handler — INFO and above
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(ch)

    _logger = logger
    logger.info("==== Session start ==== log=%s", str(log_path))
    return logger


def get_logger() -> logging.Logger:
    """Return the active logger, auto-initialising if needed."""
    if _logger is None:
        return setup_logger()
    return _logger


def get_log_path() -> str:
    """Return today's activity log path."""
    return str((_LOGS_DIR / f"{date.today().strftime('%Y-%m-%d')}.log").resolve())


def get_trade_log_path() -> str:
    """Return today's trade CSV path."""
    return str((_TRADES_DIR / f"{date.today().strftime('%Y-%m-%d')}.csv").resolve())


# ─────────────────────────────────────────────────────────────────────────────
# Basic log helpers
# ─────────────────────────────────────────────────────────────────────────────

def log(msg: str, level: str = "INFO") -> None:
    with _lock:
        get_logger().log(getattr(logging, level.upper(), logging.INFO), msg)

def log_debug(msg: str) -> None:
    log(msg, "DEBUG")

def log_warn(msg: str) -> None:
    log(msg, "WARNING")

def log_error(msg: str) -> None:
    log(msg, "ERROR")


def log_section(title: str) -> None:
    """Visual separator in the log."""
    sep = "─" * 60
    with _lock:
        lg = get_logger()
        lg.info("")
        lg.info(sep)
        lg.info("  %s", title)
        lg.info(sep)


def log_config(cfg: dict) -> None:
    log_section("STRATEGY CONFIGURATION")
    for k, v in sorted(cfg.items()):
        log(f"  {k:<30} = {v}")


def log_candle(prefix: str, candle: dict, st_result: dict) -> None:
    try:
        epoch = candle.get("bucket") or candle.get("epoch") or candle.get("time") or 0
        t = datetime.fromtimestamp(int(epoch)).strftime("%H:%M") if epoch else "--:--"
        o, h, l, c = (float(candle.get(k, 0)) for k in ("open", "high", "low", "close"))
        trend  = st_result.get("trend")  or "-"
        signal = st_result.get("signal") or "-"
        sv = st_result.get("supertrend")
        av = st_result.get("atr")
        st_str  = f"{sv:.2f}" if sv is not None else "-"
        atr_str = f"{av:.2f}" if av is not None else "-"
        log(f"{prefix} | {t} | O={o:.2f} H={h:.2f} L={l:.2f} C={c:.2f} | "
            f"Trend={trend} Signal={signal} ST={st_str} ATR={atr_str}")
    except Exception as e:
        log(f"{prefix} | candle log error: {e}", "DEBUG")


def log_signal_state(snap: dict) -> None:
    log(f"SIGNAL STATE | variant={snap.get('variant')} | "
        f"trend={snap.get('trend')} | signal={snap.get('signal')} | "
        f"active={snap.get('active')} | waiting_reentry={snap.get('waiting_reentry')} | "
        f"seeded={snap.get('indicator_seeded')} | armed={snap.get('live_action_armed')} | "
        f"sig_hi={snap.get('signal_candle_high')} | sig_lo={snap.get('signal_candle_low')}")


def log_option_snap(snap: dict) -> None:
    log(f"OPTION STATE | has_setup={snap.get('has_setup')} | "
        f"expiry={snap.get('expiry_current')} | trend={snap.get('trend')} | "
        f"short={snap.get('short_symbol')} @ {snap.get('short_premium')} | "
        f"hedge={snap.get('hedge_symbol')} @ {snap.get('hedge_premium')} | "
        f"net={snap.get('net_credit')}")
    if snap.get("last_reason"):
        log(f"OPTION REASON | {snap.get('last_reason')}", "DEBUG")


def log_paper_snap(snap: dict) -> None:
    pos = snap.get("active_position") or {}
    log(f"PAPER STATE | has_active={snap.get('has_active_position')} | "
        f"trend={pos.get('trend')} | short={pos.get('short_symbol')} | "
        f"entry_premium={pos.get('short_entry_premium')} | "
        f"sl_price={pos.get('short_sl_price')} | sl_hit={pos.get('short_sl_hit')} | "
        f"pnl_pts={pos.get('pnl_points')} | pnl_rs={pos.get('pnl_rupees')}")


def log_tick(price: float, ltt_epoch: int, interval: int = 100) -> None:
    if not hasattr(log_tick, "_count"):
        log_tick._count = 0
    log_tick._count += 1
    if log_tick._count % interval == 0:
        t = datetime.fromtimestamp(int(ltt_epoch)).strftime("%H:%M:%S")
        log(f"TICK #{log_tick._count} | price={price:.2f} | ltt={t}", "DEBUG")


# ─────────────────────────────────────────────────────────────────────────────
# Trade CSV helpers
# ─────────────────────────────────────────────────────────────────────────────

def _todays_trade_csv() -> Path:
    """Return today's trade CSV path, creating it with header if new."""
    today = date.today().strftime("%Y-%m-%d")
    path  = _TRADES_DIR / f"{today}.csv"
    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(_TRADE_HEADERS)
    return path


def _next_trade_no(date_key: str) -> int:
    if date_key not in _trade_counter:
        path = _TRADES_DIR / f"{date_key}.csv"
        count = 0
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as f:
                    count = max(sum(1 for _ in csv.reader(f)) - 1, 0)
            except Exception:
                count = 0
        _trade_counter[date_key] = count
    _trade_counter[date_key] += 1
    return _trade_counter[date_key]


# ─────────────────────────────────────────────────────────────────────────────
# Trade open / close
# ─────────────────────────────────────────────────────────────────────────────

def log_trade_open(position_snap: dict) -> None:
    """
    Call immediately after paper position opens.
    Writes an OPEN row to the trade CSV and stores entry details for later close row.
    """
    global _open_trade
    pos = position_snap.get("active_position") or position_snap

    now        = datetime.now()
    date_str   = now.strftime("%Y-%m-%d")
    date_key   = now.strftime("%Y%m%d")
    entry_time = now.strftime("%H:%M:%S")

    entry = {
        "date":             date_str,
        "entry_time":       entry_time,
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
        trade_no = _next_trade_no(date_key)
        _todays_trade_csv()   # ensure header exists
        path = _TRADES_DIR / f"{date_key}.csv"
        with path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                date_str, trade_no,
                "OPEN",
                "NIFTY",
                entry["trend"],
                entry["expiry"],
                entry["entry_time"],
                "",                      # exit_time blank on OPEN
                entry["short_symbol"],
                entry["short_entry"],
                "",                      # short_exit blank
                entry["hedge_symbol"],
                entry["hedge_entry"],
                "",                      # hedge_exit blank
                entry["net_credit_entry"],
                "",                      # net_premium_exit blank
                "",                      # pnl_points blank
                "",                      # pnl_rupees blank
                entry["lot_size"],
                "",                      # exit_reason blank
            ])
        # store trade_no for close row
        _open_trade["trade_no"]  = trade_no
        _open_trade["date_key"]  = date_key

    log_section("TRADE OPEN")
    get_logger().info(
        "TRADE OPEN | #%d | %s | trend=%s | expiry=%s | "
        "short=%s @ %s | hedge=%s @ %s | net=%s | lot=%s",
        trade_no, entry_time,
        entry["trend"], entry["expiry"],
        entry["short_symbol"], entry["short_entry"],
        entry["hedge_symbol"], entry["hedge_entry"],
        entry["net_credit_entry"], entry["lot_size"],
    )


def log_trade_close(position_snap: dict, exit_reason: str = "") -> None:
    """
    Call immediately BEFORE paper position closes (pass pre-close snapshot).
    Writes a CLOSE row to the trade CSV.
    """
    global _open_trade
    pos = position_snap.get("active_position") or position_snap

    now        = datetime.now()
    exit_time  = now.strftime("%H:%M:%S")
    date_key   = now.strftime("%Y%m%d")
    date_str   = now.strftime("%Y-%m-%d")

    with _trade_lock:
        entry    = dict(_open_trade)
        trade_no = entry.get("trade_no") or _next_trade_no(date_key)

    short_exit = pos.get("short_ltp") or pos.get("short_exit_premium", "")
    hedge_exit = pos.get("hedge_ltp") or pos.get("hedge_exit_premium", "")
    pnl_pts    = pos.get("pnl_points", "")
    pnl_rs     = pos.get("pnl_rupees", "")
    lot_size   = entry.get("lot_size") or pos.get("lot_size", 75)

    try:
        net_exit = f"{float(short_exit) - float(hedge_exit):.2f}"
    except Exception:
        net_exit = ""

    path = _TRADES_DIR / f"{date_key}.csv"
    _todays_trade_csv()
    with _trade_lock:
        with path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                date_str, trade_no,
                "CLOSE",
                "NIFTY",
                entry.get("trend", ""),
                entry.get("expiry", ""),
                entry.get("entry_time", ""),
                exit_time,
                entry.get("short_symbol", ""),
                entry.get("short_entry", ""),
                short_exit,
                entry.get("hedge_symbol", ""),
                entry.get("hedge_entry", ""),
                hedge_exit,
                entry.get("net_credit_entry", ""),
                net_exit,
                pnl_pts,
                pnl_rs,
                lot_size,
                exit_reason,
            ])
        _open_trade = {}

    log_section("TRADE CLOSE")
    get_logger().info(
        "TRADE CLOSE | #%d | %s | reason=%s | "
        "short_exit=%s | hedge_exit=%s | net_exit=%s | "
        "pnl_pts=%s | pnl_rs=%s",
        trade_no, exit_time, exit_reason,
        short_exit, hedge_exit, net_exit,
        pnl_pts, pnl_rs,
    )
    get_logger().info("Trade #%d saved → %s", trade_no, path.name)
