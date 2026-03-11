from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from option_chain_engine import OptionChainEngine, OptionSetup, PremiumRuleConfig


@dataclass
class OptionDiscoveryConfig:
    strike_step: int = 50
    min_short_premium: float = 200.0
    max_short_premium: float = 300.0
    min_hedge_premium: float = 50.0
    max_hedge_premium: float = 90.0
    min_net_credit: float = 150.0
    otm_steps: tuple = (3, 4, 5, 6)
    expiry_mode: str = "AUTO"   # AUTO / CURRENT / NEXT / FAR / TRADING_DAY


class OptionSignalBridge:
    def __init__(
        self,
        *,
        option_chain_engine: OptionChainEngine,
        discovery_cfg: Optional[OptionDiscoveryConfig] = None,
    ) -> None:
        self.option_chain_engine = option_chain_engine
        self.discovery_cfg = discovery_cfg or OptionDiscoveryConfig()

        self.last_expiry_info: Dict[str, Optional[str]] = {
            "current": None,
            "next": None,
            "far": None,
            "all": [],
        }
        self.last_setup: Optional[OptionSetup] = None
        self.last_reason: Optional[str] = None

    def refresh_master(self, force_refresh: bool = False) -> str:
        expiry_info, msg = self.option_chain_engine.get_expiry_buckets()
        self.last_expiry_info = expiry_info
        return (
            f"{msg} | Expiry ladder | "
            f"current={expiry_info.get('current')} | "
            f"next={expiry_info.get('next')} | "
            f"far={expiry_info.get('far')}"
        )

    def discover_setup(
        self,
        *,
        spot_price: float,
        trend: str,
    ) -> Tuple[Optional[OptionSetup], str]:
        trend = str(trend).upper().strip()
        if trend not in {"BUY", "SELL"}:
            self.last_setup = None
            self.last_reason = f"Invalid trend: {trend}"
            return None, self.last_reason

        expiry_info, msg = self.option_chain_engine.get_expiry_buckets()
        self.last_expiry_info = expiry_info

        expiries_to_try = self._expiries_to_try(expiry_info)
        if not expiries_to_try:
            self.last_setup = None
            self.last_reason = f"{msg} | No expiry buckets available"
            return None, self.last_reason

        rule_cfg = PremiumRuleConfig(
            min_short_premium=float(self.discovery_cfg.min_short_premium),
            max_short_premium=float(self.discovery_cfg.max_short_premium),
            min_hedge_premium=float(self.discovery_cfg.min_hedge_premium),
            max_hedge_premium=float(self.discovery_cfg.max_hedge_premium),
            min_net_credit=float(self.discovery_cfg.min_net_credit),
        )

        failures = []

        for expiry, bucket_type in expiries_to_try:
            setup, reason = self.option_chain_engine.discover_setup_for_expiry(
                expiry=expiry,
                spot_price=float(spot_price),
                trend=trend,
                otm_steps=tuple(self.discovery_cfg.otm_steps),
                rule_cfg=rule_cfg,
                expiry_bucket_type=bucket_type,
            )

            if setup is not None:
                self.last_setup = setup
                self.last_reason = (
                    f"{reason} | expiry={expiry} | bucket={bucket_type} | trend={trend} | spot={float(spot_price):.2f}"
                )
                return setup, self.last_reason

            failures.append(f"{expiry}({bucket_type}) | {reason}")

        self.last_setup = None
        self.last_reason = (
            f"No valid option setup found | trend={trend} | spot={float(spot_price):.2f} | "
            + " || ".join(failures)
        )
        return None, self.last_reason

    def snapshot(self) -> Dict[str, object]:
        setup = self.last_setup
        expiry_info = self.last_expiry_info or {}

        return {
            "last_reason": self.last_reason,
            "expiry_current": expiry_info.get("current"),
            "expiry_next": expiry_info.get("next"),
            "expiry_far": expiry_info.get("far"),
            "has_setup": setup is not None,
            "trend": setup.trend if setup else None,
            "expiry_text": setup.expiry_text if setup else None,
            "short_security_id": setup.short_contract.security_id if setup else None,
            "short_symbol": setup.short_contract.trading_symbol if setup else None,
            "short_option_type": setup.short_contract.option_type if setup else None,
            "short_strike": setup.short_contract.strike if setup else None,
            "short_premium": setup.short_premium if setup else None,
            "hedge_security_id": setup.hedge_contract.security_id if setup else None,
            "hedge_symbol": setup.hedge_contract.trading_symbol if setup else None,
            "hedge_option_type": setup.hedge_contract.option_type if setup else None,
            "hedge_strike": setup.hedge_contract.strike if setup else None,
            "hedge_premium": setup.hedge_premium if setup else None,
            "net_credit": setup.net_credit if setup else None,
            "otm_step": setup.otm_step if setup else None,
            "hedge_distance_steps": setup.hedge_distance_steps if setup else None,
        }

    def _expiries_to_try(self, expiry_info: Dict[str, Optional[str]]) -> List[Tuple[str, str]]:
        """Return list of (expiry_str, bucket_type) to try in order."""
        mode = str(self.discovery_cfg.expiry_mode).upper().strip()

        if mode == "CURRENT":
            return [(x, "current") for x in [expiry_info.get("current")] if x]
        if mode == "NEXT":
            return [(x, "next") for x in [expiry_info.get("next")] if x]
        if mode == "FAR":
            return [(x, "far") for x in [expiry_info.get("far")] if x]
        if mode == "TRADING_DAY":
            return self._trading_day_expiries(expiry_info)

        # AUTO: current -> next -> far
        return [
            (x, key)
            for key in ("current", "next", "far")
            for x in [expiry_info.get(key)]
            if x
        ]

    def _trading_day_expiries(self, expiry_info: Dict[str, Optional[str]]) -> List[Tuple[str, str]]:
        """
        Strategy rule: 1st-15th trading day of month → current month expiry.
        After 15th trading day → next month expiry.
        Fallback to far month if premium unavailable.
        """
        try:
            from rollover_engine import NSE_HOLIDAYS, count_trading_days_this_month
            td_count = count_trading_days_this_month(date.today(), NSE_HOLIDAYS)
            primary_key = "current" if td_count <= 15 else "next"
        except Exception:
            primary_key = "current"

        result: List[Tuple[str, str]] = []
        primary = expiry_info.get(primary_key)
        if primary:
            result.append((primary, primary_key))
        far = expiry_info.get("far")
        if far:
            result.append((far, "far"))
        return result