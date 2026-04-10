from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import requests


@dataclass
class ChainLeg:
    security_id: str
    trading_symbol: str
    option_type: str          # CE / PE
    strike: float
    last_price: float
    expiry_text: str


@dataclass
class OptionSetup:
    trend: str
    expiry_text: str
    short_contract: ChainLeg
    short_premium: float
    hedge_contract: ChainLeg
    hedge_premium: float
    net_credit: float
    otm_step: int
    hedge_distance_steps: int


@dataclass
class PremiumRuleConfig:
    min_short_premium: float = 200.0
    max_short_premium: float = 300.0
    min_hedge_premium: float = 50.0
    max_hedge_premium: float = 90.0
    min_net_credit: float = 150.0


class OptionChainEngine:
    EXPIRYLIST_URL = "https://api.dhan.co/v2/optionchain/expirylist"
    OPTIONCHAIN_URL = "https://api.dhan.co/v2/optionchain"

    def __init__(
        self,
        client_id: str,
        access_token: str,
        underlying_scrip: int = 13,
        underlying_seg: str = "IDX_I",
        atm_step: int = 100,
        strike_step: int = 50,
        min_unique_request_gap_sec: float = 3.2,
        cache_ttl_sec: float = 2.5,
    ) -> None:
        self.client_id = str(client_id).strip()
        self.access_token = str(access_token).strip()
        self.underlying_scrip = int(underlying_scrip)
        self.underlying_seg = str(underlying_seg).strip()
        self.atm_step = int(atm_step)        # ATM rounding + OTM offset base (100 pts)
        self.strike_step = int(strike_step)  # chain key search step (50 pts, Dhan interval)

        self.min_unique_request_gap_sec = float(min_unique_request_gap_sec)
        self.cache_ttl_sec = float(cache_ttl_sec)

        self._last_request_ts: float = 0.0
        self._expiry_cache: Optional[List[str]] = None
        self._expiry_cache_ts: float = 0.0
        self._chain_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": self.access_token,
            "client-id": self.client_id,
        }

    def _respect_rate_limit(self) -> None:
        now = time.time()
        gap = now - self._last_request_ts
        if gap < self.min_unique_request_gap_sec:
            time.sleep(self.min_unique_request_gap_sec - gap)

    def _post_json(self, url: str, payload: Dict[str, Any], retries: int = 4) -> Dict[str, Any]:
        last_exc = None
        for attempt in range(retries):
            self._respect_rate_limit()
            try:
                r = requests.post(url, headers=self._headers(), json=payload, timeout=20)
                self._last_request_ts = time.time()
                if r.status_code in (500, 502, 503, 504):
                    wait = 2 ** attempt  # 1s, 2s, 4s, 8s
                    time.sleep(wait)
                    last_exc = Exception(f"HTTP {r.status_code} {r.reason} (attempt {attempt+1}/{retries})")
                    continue
                r.raise_for_status()
                return r.json()
            except requests.exceptions.Timeout:
                last_exc = Exception(f"Timeout (attempt {attempt+1}/{retries})")
                time.sleep(2 ** attempt)
            except requests.exceptions.ConnectionError as e:
                last_exc = Exception(f"Connection error: {e} (attempt {attempt+1}/{retries})")
                time.sleep(2 ** attempt)
            except Exception as e:
                self._last_request_ts = time.time()
                raise
        raise last_exc or Exception("Request failed after retries")

    # ------------------------------------------------------------------
    # Expiry list
    # ------------------------------------------------------------------
    def get_expiry_list(self, force_refresh: bool = False) -> Tuple[List[str], str]:
        if (
            not force_refresh
            and self._expiry_cache is not None
            and (time.time() - self._expiry_cache_ts) <= 60.0
        ):
            return list(self._expiry_cache), f"Expiry cache hit: {len(self._expiry_cache)} expiries"

        payload = {
            "UnderlyingScrip": self.underlying_scrip,
            "UnderlyingSeg": self.underlying_seg,
        }

        try:
            data = self._post_json(self.EXPIRYLIST_URL, payload)
        except Exception as e:
            return [], f"Expiry list fetch failed (will retry on next candle): {e}"

        expiries = data.get("data", [])
        if not isinstance(expiries, list):
            return [], "Expiry list response malformed"

        expiries = [str(x) for x in expiries if str(x).strip()]
        if not expiries:
            return [], "Expiry list returned empty — will retry on next candle"

        self._expiry_cache = expiries
        self._expiry_cache_ts = time.time()
        return list(expiries), f"Expiry list ok: {len(expiries)} expiries"

    # ------------------------------------------------------------------
    # Option chain
    # ------------------------------------------------------------------
    def get_option_chain(self, expiry: str, force_refresh: bool = False) -> Tuple[Optional[Dict[str, Any]], str]:
        expiry = str(expiry).strip()
        if not expiry:
            return None, "Option chain fetch skipped: empty expiry"

        cached = self._chain_cache.get(expiry)
        if cached and not force_refresh:
            ts, data = cached
            if (time.time() - ts) <= self.cache_ttl_sec:
                return data, f"Option chain cache hit: {expiry}"

        payload = {
            "UnderlyingScrip": self.underlying_scrip,
            "UnderlyingSeg": self.underlying_seg,
            "Expiry": expiry,
        }

        try:
            data = self._post_json(self.OPTIONCHAIN_URL, payload)
        except Exception as e:
            return None, f"Option chain fetch failed for {expiry} (will retry): {e}"

        self._chain_cache[expiry] = (time.time(), data)
        root = data.get("data", {})
        oc = root.get("oc", {}) if isinstance(root, dict) else {}
        n = len(oc) if isinstance(oc, dict) else 0
        return data, f"Option chain ok: expiry={expiry} strikes={n}"

    # ------------------------------------------------------------------
    # Expiry classification
    # ------------------------------------------------------------------
    def get_expiry_buckets(self, force_refresh: bool = False) -> Tuple[Dict[str, Optional[str]], str]:
        expiries, msg = self.get_expiry_list(force_refresh=force_refresh)
        if not expiries:
            return {"current": None, "next": None, "far": None, "all": []}, msg

        # Parse all expiry strings into (date, str) pairs
        parsed = []
        for x in expiries:
            try:
                parsed.append((datetime.strptime(x, "%Y-%m-%d").date(), x))
            except Exception:
                continue

        parsed.sort(key=lambda t: t[0])

        today = date.today()

        # ── Group by (year, month) and take the LAST date in each group ──────
        # Last expiry of each month = monthly expiry (last Thursday of month)
        from collections import OrderedDict
        month_groups: dict = OrderedDict()
        for d, x in parsed:
            key = (d.year, d.month)
            # keep replacing — last one in sorted order wins = monthly expiry
            month_groups[key] = (d, x)

        # Sorted list of monthly expiries: [(date, str), ...]
        monthly = sorted(month_groups.values(), key=lambda t: t[0])

        # current = last expiry of current month (or nearest future month)
        current = None
        for d, x in monthly:
            if d >= today:
                current = (d, x)
                break
        # If all monthly expiries are in the past, take the last one
        if current is None and monthly:
            current = monthly[-1]

        # next = last expiry of the month immediately after current's month
        next_exp = None
        if current is not None:
            for d, x in monthly:
                if d.year > current[0].year or (
                    d.year == current[0].year and d.month > current[0].month
                ):
                    next_exp = (d, x)
                    break

        # far = last expiry of the month immediately after next's month
        far_exp = None
        if next_exp is not None:
            for d, x in monthly:
                if d.year > next_exp[0].year or (
                    d.year == next_exp[0].year and d.month > next_exp[0].month
                ):
                    far_exp = (d, x)
                    break

        return {
            "current": current[1] if current else None,
            "next": next_exp[1] if next_exp else None,
            "far": far_exp[1] if far_exp else None,
            "all": [x for _, x in parsed],
        }, msg

    # ------------------------------------------------------------------
    # Setup selection
    # ------------------------------------------------------------------
    def discover_setup_for_expiry(
        self,
        *,
        expiry: str,
        spot_price: float,
        trend: str,
        otm_steps: Tuple[int, ...],   # kept for signature compat — min start step = otm_steps[0] or 3
        rule_cfg: PremiumRuleConfig,
        expiry_bucket_type: str = "current",
    ) -> Tuple[Optional[OptionSetup], str]:
        trend = str(trend).upper().strip()
        if trend not in {"BUY", "SELL"}:
            return None, f"Invalid trend: {trend}"

        data, chain_msg = self.get_option_chain(expiry)
        if not data:
            return None, chain_msg

        root = data.get("data", {})
        oc = root.get("oc", {})
        if not isinstance(oc, dict) or not oc:
            return None, f"{chain_msg} | No option chain rows"

        atm = self.compute_atm_strike(spot_price)
        opt_type = "PE" if trend == "BUY" else "CE"

        # Rounding rule: 100pts/step for current/next month, 500pts/step for far month
        otm_offset = 500 if str(expiry_bucket_type).lower() == "far" else 100

        # Minimum starting OTM step (always 3, or first value in otm_steps)
        min_step = int(otm_steps[0]) if otm_steps else 3

        debug_lines = [
            chain_msg,
            f"ATM={atm:.0f}",
            f"OptionType={opt_type}",
            f"OTMOffset={otm_offset}pts/step",
            f"WalkFrom={min_step}OTM outward",
        ]

        # ── Build sorted list of all valid round-figure OTM strikes from chain ──
        # A strike is valid if:
        #   1. It is OTM relative to ATM (lower for PE, higher for CE)
        #   2. It is a round figure (multiple of otm_offset)
        #   3. It is at least min_step * otm_offset away from ATM
        #   4. It exists in the option chain

        otm_strikes = []  # list of (step_number, strike_value)
        for strike_key in oc.keys():
            sv = self._safe_float(strike_key)
            if sv is None:
                continue
            # Must be round figure (multiple of otm_offset)
            if abs(round(sv / otm_offset) * otm_offset - sv) > 0.01:
                continue
            if opt_type == "PE":
                if sv >= atm:
                    continue
                step = int(round((atm - sv) / otm_offset))
            else:  # CE
                if sv <= atm:
                    continue
                step = int(round((sv - atm) / otm_offset))
            if step < min_step:
                continue
            otm_strikes.append((step, sv))

        # Walk outward from min_step
        otm_strikes.sort(key=lambda x: x[0])

        for step, strike in otm_strikes:
            strike_key = f"{float(strike):.6f}"
            row = oc.get(strike_key)
            if not isinstance(row, dict):
                debug_lines.append(f"{step}OTM {opt_type} {strike:.0f}: missing in chain")
                continue

            leg = row.get(opt_type.lower(), {})
            if not isinstance(leg, dict):
                debug_lines.append(f"{step}OTM {opt_type} {strike:.0f}: leg missing")
                continue

            short_lp  = self._safe_float(leg.get("last_price"))
            short_sid = str(leg.get("security_id", "")).strip()

            if short_lp is None:
                debug_lines.append(f"{step}OTM {opt_type} {strike:.0f}: premium missing")
                continue

            if short_lp < rule_cfg.min_short_premium:
                debug_lines.append(
                    f"{step}OTM {opt_type} {strike:.0f}: short={short_lp:.2f} below min {rule_cfg.min_short_premium:.2f}"
                )
                # Going further OTM will only be cheaper — stop searching this expiry
                break

            if short_lp > rule_cfg.max_short_premium:
                debug_lines.append(
                    f"{step}OTM {opt_type} {strike:.0f}: short={short_lp:.2f} above max {rule_cfg.max_short_premium:.2f}"
                )
                # Keep walking — next strike will be cheaper
                continue

            short_leg = ChainLeg(
                security_id=short_sid,
                trading_symbol=self._synthetic_symbol(expiry, strike, opt_type),
                option_type=opt_type,
                strike=float(strike),
                last_price=float(short_lp),
                expiry_text=expiry,
            )

            hedge_leg, hedge_steps, hedge_reason = self._find_hedge_leg(
                oc=oc,
                expiry=expiry,
                short_strike=float(strike),
                short_premium=float(short_lp),
                option_type=opt_type,
                rule_cfg=rule_cfg,
                otm_offset=otm_offset,
            )

            if hedge_leg is None:
                debug_lines.append(
                    f"{step}OTM {opt_type} {strike:.0f}: short OK @ {short_lp:.2f} | hedge rejected — {hedge_reason}"
                )
                continue

            net_credit = short_leg.last_price - hedge_leg.last_price
            if net_credit < rule_cfg.min_net_credit:
                debug_lines.append(
                    f"{step}OTM {opt_type} {strike:.0f}: net credit {net_credit:.2f} below min {rule_cfg.min_net_credit:.2f}"
                )
                continue

            return (
                OptionSetup(
                    trend=trend,
                    expiry_text=expiry,
                    short_contract=short_leg,
                    short_premium=short_leg.last_price,
                    hedge_contract=hedge_leg,
                    hedge_premium=hedge_leg.last_price,
                    net_credit=net_credit,
                    otm_step=step,
                    hedge_distance_steps=hedge_steps,
                ),
                (
                    f"Valid setup | "
                    f"short={short_leg.trading_symbol} @ {short_leg.last_price:.2f} | "
                    f"hedge={hedge_leg.trading_symbol} @ {hedge_leg.last_price:.2f} | "
                    f"net={net_credit:.2f}"
                ),
            )

        return None, " | ".join(debug_lines + ["No valid option setup matched premium rules"])

    def compute_atm_strike(self, spot_price: float) -> float:
        spot_price = float(spot_price)
        return round(spot_price / self.atm_step) * self.atm_step

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _find_hedge_leg(
        self,
        *,
        oc: Dict[str, Any],
        expiry: str,
        short_strike: float,
        short_premium: float,
        option_type: str,
        rule_cfg: PremiumRuleConfig,
        otm_offset: int = 100,
    ) -> Tuple[Optional[ChainLeg], int, str]:
        """
        Walk all strikes further OTM than short_strike outward.
        Only consider round-figure strikes (multiples of otm_offset).

        Key logic:
        - Walk from closest OTM outward
        - Skip if hedge premium above max_hedge (too expensive)
        - Stop walking when hedge premium drops below min_hedge AND
          net_credit (short - hedge) is still below min_net_credit
          (going further OTM only makes hedge cheaper = higher net credit,
           so if net credit is already >= min_net_credit we stop at first valid)
        - Accept first hedge where:
            min_hedge <= hedge_premium <= max_hedge
            AND net_credit >= min_net_credit
        """
        option_type = option_type.upper().strip()
        candidates = []

        for strike_key, row in oc.items():
            if not isinstance(row, dict):
                continue
            strike_val = self._safe_float(strike_key)
            if strike_val is None:
                continue

            # Must be round figure (multiple of otm_offset)
            if abs(round(strike_val / otm_offset) * otm_offset - strike_val) > 0.01:
                continue

            if option_type == "PE" and strike_val < short_strike:
                diff_steps = int(round((short_strike - strike_val) / otm_offset))
            elif option_type == "CE" and strike_val > short_strike:
                diff_steps = int(round((strike_val - short_strike) / otm_offset))
            else:
                continue

            leg = row.get(option_type.lower(), {})
            if not isinstance(leg, dict):
                continue

            lp  = self._safe_float(leg.get("last_price"))
            sid = str(leg.get("security_id", "")).strip()
            if lp is None:
                continue

            candidates.append((diff_steps, strike_val, sid, lp))

        # Sort by distance — closest first (walk outward)
        candidates.sort(key=lambda x: x[0])

        reject_logs = []

        for diff_steps, strike_val, sid, lp in candidates:
            net_credit = short_premium - lp

            # Hedge too expensive — keep walking (further OTM = cheaper)
            if lp > rule_cfg.max_hedge_premium:
                reject_logs.append(
                    f"hedge {option_type} {strike_val:.0f} @ {lp:.2f} above max {rule_cfg.max_hedge_premium:.2f}"
                )
                continue

            # Hedge below strict minimum floor — STOP, going further only gets worse
            if lp < rule_cfg.min_hedge_premium:
                reject_logs.append(
                    f"hedge {option_type} {strike_val:.0f} @ {lp:.2f} below min {rule_cfg.min_hedge_premium:.2f} — stopping"
                )
                break

            # Hedge premium is valid but net credit not enough yet
            # Keep walking further OTM — cheaper hedge = higher net credit
            if net_credit < rule_cfg.min_net_credit:
                reject_logs.append(
                    f"hedge {option_type} {strike_val:.0f} @ {lp:.2f} | "
                    f"net={net_credit:.2f} below min {rule_cfg.min_net_credit:.2f} — walking further"
                )
                continue

            # ── Both conditions pass → valid hedge ──
            return (
                ChainLeg(
                    security_id=sid,
                    trading_symbol=self._synthetic_symbol(expiry, strike_val, option_type),
                    option_type=option_type,
                    strike=float(strike_val),
                    last_price=float(lp),
                    expiry_text=expiry,
                ),
                diff_steps,
                f"hedge selected {strike_val:.0f} @ {lp:.2f} | net={net_credit:.2f}",
            )

        if not candidates:
            return None, 0, "no further OTM hedge candidates found"

        return None, 0, "; ".join(reject_logs) if reject_logs else "no hedge matched rules"

    def _synthetic_symbol(self, expiry: str, strike: float, option_type: str) -> str:
        return f"NIFTY {expiry} {int(round(strike))} {option_type}"

    def _safe_float(self, x: Any) -> Optional[float]:
        try:
            return float(x)
        except Exception:
            return None