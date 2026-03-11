# Balfund — Supertrend Option Selling Strategy

**Balfund Trading Private Limited**

A desktop GUI application for running the NIFTY Supertrend directional option selling strategy
via the Dhan API. Packaged as a single Windows `.exe` for client distribution.

---

## Features

- **4 Strategy Variants** — SL%, Supertrend change, Candle breach, combinations
- **All timeframes** — 1m / 5m / 60m / 120m
- **Auto token management** — TOTP-based, renews daily
- **Live option chain scanning** — Dhan v2 API, ATM=100pt, 3–6 OTM steps
- **Paper trading** — open/close, live MTM via WebSocket, SL alerts
- **2 Rollover variants** — 3rd weekly expiry or 4 days before monthly
- **Trading-day aware expiry selection** — 1–15th = current month, 16th+ = next month
- **All parameters configurable from GUI**

---

## GUI Tabs

| Tab | Description |
|-----|-------------|
| 🔑 Token Manager | Enter credentials, generate TOTP token, load from `.env` |
| ⚙ Strategy Setup | Set variant, timeframe, ST params, SL%, rollover, premium rules |
| 📈 Live Dashboard | Real-time candles, signal state, option setup, paper P&L, event log |

---

## Strategy Parameters

| Parameter | Options | Default |
|-----------|---------|---------|
| Variant | 1 / 2 / 3 / 4 | 3 |
| Timeframe | 1 / 5 / 60 / 120 min | 60 |
| ST Period | 8 / 10 | 10 |
| ST Multiplier | 3.0 / 3.5 / 4.0 | 3.0 |
| SL % | 25–40% | 30% |
| Expiry Mode | AUTO / TRADING_DAY / CURRENT / NEXT / FAR | AUTO |
| OTM Steps | 3,4,5,6 (any combination) | 3,4,5,6 |
| Rollover | 1 = 3rd weekly / 2 = 4td before expiry | 2 |
| Short Premium | ₹200–₹300 | ₹200–₹300 |
| Hedge Premium | ₹50–₹90 | ₹50–₹90 |
| Net Credit | ₹150 min | ₹150 |

---

## Building the EXE

### Option A — GitHub Actions (Recommended)

1. Push to `main` branch
2. Actions automatically builds on `windows-latest`
3. Download `.exe` from **Releases** tab or **Actions → Artifacts**

### Option B — Local build (Windows only)

```bash
pip install -r requirements.txt
pyinstaller BalfundSupertrend.spec
# Output: dist/BalfundSupertrend.exe
```

---

## Setup for Clients

1. Download `BalfundSupertrend.exe`
2. Double-click to run — no Python installation needed
3. Enter Dhan credentials in **Token Manager** tab
4. Configure strategy in **Strategy Setup** tab
5. Click **▶ Start**

---

## .env File (Optional)

Create `.env` in the same folder as the `.exe` to pre-fill credentials:

```
DHAN_CLIENT_ID=your_client_id
DHAN_PIN=your_4digit_pin
DHAN_TOTP_SECRET=your_totp_secret
DHAN_ACCESS_TOKEN=   # auto-filled after token generation
```

---

*Balfund Trading Private Limited — Internal use only. Backtest before live trading.*
