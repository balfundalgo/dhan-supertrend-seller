from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

import requests


def fetch_intraday_1m_history(
    *,
    client_id: str,
    access_token: str,
    security_id: str,
    exchange_segment: str,
    lookback_days: int,
    limit: int,
    instrument: str = "INDEX",
    interval: int = 1,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Fetch historical candles from Dhan at the specified interval (minutes).

    Default interval=1 fetches 1m bars (legacy behaviour).
    Pass interval=5 to fetch 5m bars directly — gives exact same OHLC
    as Dhan's live WS data, avoiding Supertrend discrepancy when starting
    mid-session.

    Returns: (candles, message)
    candles format:
        {
            "bucket": epoch_seconds,
            "open": float,
            "high": float,
            "low": float,
            "close": float
        }
    """
    url = "https://api.dhan.co/v2/charts/intraday"

    now = datetime.now()
    from_dt = now - timedelta(days=max(1, int(lookback_days)))

    headers = {
        "Content-Type": "application/json",
        "access-token": str(access_token),
        "client-id": str(client_id),
    }

    payload = {
        "securityId": str(security_id),
        "exchangeSegment": str(exchange_segment),
        "instrument": str(instrument),
        "expiryCode": 0,
        "oi": False,
        "interval": str(int(interval)),
        "fromDate": from_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate": now.strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
    except Exception as e:
        return [], f"History fetch failed: {e}"

    if r.status_code != 200:
        body = (r.text or "")[:400]
        return [], f"History fetch failed: HTTP {r.status_code} {body}"

    try:
        data = r.json()
    except Exception as e:
        return [], f"History JSON parse failed: {e}"

    ts = data.get("timestamp") or data.get("timestamps") or data.get("t") or []
    o = data.get("open") or data.get("o") or []
    h = data.get("high") or data.get("h") or []
    l = data.get("low") or data.get("l") or []
    c = data.get("close") or data.get("c") or []

    n = min(len(ts), len(o), len(h), len(l), len(c))
    if n <= 0:
        return [], "History fetch returned no candles"

    out: List[Dict[str, Any]] = []
    for i in range(n):
        try:
            out.append(
                {
                    "bucket": int(ts[i]),
                    "open": float(o[i]),
                    "high": float(h[i]),
                    "low": float(l[i]),
                    "close": float(c[i]),
                }
            )
        except Exception:
            continue

    if int(limit) > 0 and len(out) > int(limit):
        out = out[-int(limit):]

    interval_label = f"{int(interval)}m"
    return out, f"History seeded with {len(out)} x {interval_label} candles"

    """
    Fetch 1-minute historical candles from Dhan.
    Returns: (candles, message)
    candles format:
        {
            "bucket": epoch_seconds,
            "open": float,
            "high": float,
            "low": float,
            "close": float
        }
    """
    url = "https://api.dhan.co/v2/charts/intraday"

    now = datetime.now()
    from_dt = now - timedelta(days=max(1, int(lookback_days)))

    headers = {
        "Content-Type": "application/json",
        "access-token": str(access_token),
        "client-id": str(client_id),
    }

    payload = {
        "securityId": str(security_id),
        "exchangeSegment": str(exchange_segment),
        "instrument": str(instrument),
        "expiryCode": 0,
        "oi": False,
        "interval": "1",
        "fromDate": from_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate": now.strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
    except Exception as e:
        return [], f"History fetch failed: {e}"

    if r.status_code != 200:
        body = (r.text or "")[:400]
        return [], f"History fetch failed: HTTP {r.status_code} {body}"

    try:
        data = r.json()
    except Exception as e:
        return [], f"History JSON parse failed: {e}"

    ts = data.get("timestamp") or data.get("timestamps") or data.get("t") or []
    o = data.get("open") or data.get("o") or []
    h = data.get("high") or data.get("h") or []
    l = data.get("low") or data.get("l") or []
    c = data.get("close") or data.get("c") or []

    n = min(len(ts), len(o), len(h), len(l), len(c))
    if n <= 0:
        return [], "History fetch returned no candles"

    out: List[Dict[str, Any]] = []
    for i in range(n):
        try:
            out.append(
                {
                    "bucket": int(ts[i]),
                    "open": float(o[i]),
                    "high": float(h[i]),
                    "low": float(l[i]),
                    "close": float(c[i]),
                }
            )
        except Exception:
            continue

    if int(limit) > 0 and len(out) > int(limit):
        out = out[-int(limit):]

    return out, f"History seeded with {len(out)} x 1m candles"