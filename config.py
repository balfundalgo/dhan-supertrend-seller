from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class AppConfig:
    # Spot / dashboard / signal engine
    symbol_name: str
    exchange_segment: str
    security_id: str

    timeframe_minutes: int
    variant: int

    st_period: int
    st_multiplier: float
    sl_percent: float

    state_file: str

    history_lookback_days: int
    history_limit: int

    # Phase 3 option discovery
    option_strike_step: int
    option_otm_steps: tuple[int, ...]
    option_expiry_mode: str

    min_short_premium: float
    max_short_premium: float
    min_hedge_premium: float
    max_hedge_premium: float
    min_net_credit: float

    # Rollover
    rollover_variant: int   # 1 = 3rd weekly expiry, 2 = 4 trading days before monthly

    # Variant 5 dual-TF
    lower_tf_minutes: int   # LTF for trailing exit (e.g. 15)


def _prompt_timeframe(default_value: int = 60) -> int:
    allowed = {1, 5, 30, 45, 60, 120}
    while True:
        raw = input("Select timeframe in minutes [1 / 5 / 30 / 45 / 60 / 120]: ").strip()
        if not raw:
            return default_value
        try:
            tf = int(raw)
            if tf in allowed:
                return tf
        except Exception:
            pass
        print("Invalid timeframe. Please enter 1, 5, 30, 45, 60, or 120.")


def _prompt_variant(default_value: int = 1) -> int:
    allowed = {1, 2, 3, 4}
    while True:
        raw = input("Select strategy variant [1 / 2 / 3 / 4]: ").strip()
        if not raw:
            return default_value
        try:
            v = int(raw)
            if v in allowed:
                return v
        except Exception:
            pass
        print("Invalid variant. Please enter 1, 2, 3, or 4.")


def _parse_otm_steps(raw: str) -> tuple[int, ...]:
    text = str(raw or "").strip()
    if not text:
        return (3, 4, 5, 6)

    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except Exception:
            continue

    out = [x for x in out if x > 0]
    return tuple(out) if out else (3, 4, 5, 6)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dhan Supertrend Strategy Dashboard")

    # Existing Phase 2 args
    parser.add_argument("--timeframe", type=int, choices=[1, 5, 30, 45, 60, 120], default=None)
    parser.add_argument("--variant", type=int, choices=[1, 2, 3, 4], default=None)

    parser.add_argument("--st-period", type=int, default=None)
    parser.add_argument("--st-multiplier", type=float, default=None)
    parser.add_argument("--sl-percent", type=float, default=None)

    parser.add_argument("--history-lookback-days", type=int, default=None)
    parser.add_argument("--history-limit", type=int, default=None)

    # Phase 3 option discovery args
    parser.add_argument("--option-strike-step", type=int, default=None)
    parser.add_argument("--option-otm-steps", type=str, default=None)
    parser.add_argument("--option-expiry-mode", type=str, choices=["AUTO", "CURRENT", "NEXT", "FAR", "TRADING_DAY"], default=None)

    parser.add_argument("--min-short-premium", type=float, default=None)
    parser.add_argument("--max-short-premium", type=float, default=None)
    parser.add_argument("--min-hedge-premium", type=float, default=None)
    parser.add_argument("--max-hedge-premium", type=float, default=None)
    parser.add_argument("--min-net-credit", type=float, default=None)

    parser.add_argument("--rollover-variant", type=int, choices=[1, 2], default=None,
                        help="1=Roll on 3rd weekly expiry @ 3PM | 2=Roll 4 trading days before monthly @ 3PM")
    parser.add_argument("--lower-tf", type=int,
                        choices=[1, 5, 10, 15, 30, 45, 60, 120], default=None,
                        help="Lower timeframe for Variant 5 dual-TF trailing (default: 15)")

    return parser


def load_config(args: argparse.Namespace) -> AppConfig:
    load_dotenv()

    # Existing Phase 2 config
    timeframe_minutes = args.timeframe if args.timeframe is not None else _prompt_timeframe(
        int(os.getenv("DEFAULT_TIMEFRAME_MINUTES", "1"))
    )
    variant = args.variant if args.variant is not None else _prompt_variant(
        int(os.getenv("DEFAULT_VARIANT", "1"))
    )

    st_period = args.st_period if args.st_period is not None else int(os.getenv("ST_PERIOD", "10"))
    st_multiplier = (
        args.st_multiplier if args.st_multiplier is not None else float(os.getenv("ST_MULTIPLIER", "3.0"))
    )
    sl_percent = args.sl_percent if args.sl_percent is not None else float(os.getenv("SL_PERCENT", "30.0"))

    history_lookback_days = (
        args.history_lookback_days
        if args.history_lookback_days is not None
        else int(os.getenv("HISTORY_LOOKBACK_DAYS", "5"))
    )
    history_limit = (
        args.history_limit
        if args.history_limit is not None
        else int(os.getenv("HISTORY_LIMIT", "1500"))
    )

    # Phase 3 config
    option_strike_step = (
        args.option_strike_step
        if args.option_strike_step is not None
        else int(os.getenv("OPTION_STRIKE_STEP", "50"))
    )

    option_otm_steps = _parse_otm_steps(
        args.option_otm_steps if args.option_otm_steps is not None else os.getenv("OPTION_OTM_STEPS", "3,4,5,6")
    )

    option_expiry_mode = (
        str(args.option_expiry_mode).upper()
        if args.option_expiry_mode is not None
        else str(os.getenv("OPTION_EXPIRY_MODE", "AUTO")).upper()
    )
    if option_expiry_mode not in {"AUTO", "CURRENT", "NEXT", "FAR", "TRADING_DAY"}:
        option_expiry_mode = "AUTO"

    min_short_premium = (
        args.min_short_premium
        if args.min_short_premium is not None
        else float(os.getenv("MIN_SHORT_PREMIUM", "200"))
    )
    max_short_premium = (
        args.max_short_premium
        if args.max_short_premium is not None
        else float(os.getenv("MAX_SHORT_PREMIUM", "300"))
    )
    min_hedge_premium = (
        args.min_hedge_premium
        if args.min_hedge_premium is not None
        else float(os.getenv("MIN_HEDGE_PREMIUM", "50"))
    )
    max_hedge_premium = (
        args.max_hedge_premium
        if args.max_hedge_premium is not None
        else float(os.getenv("MAX_HEDGE_PREMIUM", "90"))
    )
    min_net_credit = (
        args.min_net_credit
        if args.min_net_credit is not None
        else float(os.getenv("MIN_NET_CREDIT", "150"))
    )

    rollover_variant = (
        args.rollover_variant
        if args.rollover_variant is not None
        else int(os.getenv("ROLLOVER_VARIANT", "2"))
    )
    if rollover_variant not in (1, 2):
        rollover_variant = 2

    lower_tf_minutes = (
        args.lower_tf
        if hasattr(args, "lower_tf") and args.lower_tf is not None
        else int(os.getenv("LOWER_TF_MINUTES", "15"))
    )
    if lower_tf_minutes not in (1, 5, 10, 15, 30, 45, 60, 120):
        lower_tf_minutes = 15

    state_dir = Path("state")
    state_dir.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        # Existing
        symbol_name=os.getenv("SYMBOL_NAME", "NIFTY 50"),
        exchange_segment=os.getenv("EXCHANGE_SEGMENT", "IDX_I"),
        security_id=os.getenv("NIFTY_SECURITY_ID", "13"),
        timeframe_minutes=int(timeframe_minutes),
        variant=int(variant),
        st_period=int(st_period),
        st_multiplier=float(st_multiplier),
        sl_percent=float(sl_percent),
        state_file=str(state_dir / "runtime_state.json"),
        history_lookback_days=int(history_lookback_days),
        history_limit=int(history_limit),

        # Phase 3
        option_strike_step=int(option_strike_step),
        option_otm_steps=tuple(option_otm_steps),
        option_expiry_mode=str(option_expiry_mode),

        min_short_premium=float(min_short_premium),
        max_short_premium=float(max_short_premium),
        min_hedge_premium=float(min_hedge_premium),
        max_hedge_premium=float(max_hedge_premium),
        min_net_credit=float(min_net_credit),

        rollover_variant=int(rollover_variant),
        lower_tf_minutes=int(lower_tf_minutes),
    )