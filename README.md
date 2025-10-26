# TradingView → Dhan Options Bot

This project deploys a FastAPI application that listens to TradingView webhook
alerts and trades NIFTY options on the Dhan platform.  Incoming alerts trigger
ATM option buying on the nearest expiry; the bot automatically books profit once
the option premium moves 10 points and will close an open trade if the
opposite-side alert fires.

## Features

- FastAPI webhook endpoint suitable for TradingView alerts.
- Automatically resolves the nearest weekly expiry and ATM strike for NIFTY.
- Connects to DhanHQ v2 APIs for market data and order placement.
- Places a take-profit limit order 10 points above the entry premium.
- Reverse signal immediately squares off the active position.
- Polls Dhan order status to confirm fills before calculating the profit target.

## Project layout

| File | Description |
| ---- | ----------- |
| `dhan_client.py` | Minimal REST client for the DhanHQ v2 API. |
| `tradingview_delta_bot.py` | FastAPI application that handles TradingView webhooks and orchestrates trades. |
| `sample_webhook.json` | Example payload to test the webhook endpoint locally. |
| `requirements.txt` | Python dependencies. |
| `Procfile` | Process definition for deployment (e.g. on cPanel passenger). |

## Prerequisites

1. Python 3.10 or newer.
2. Dhan API credentials:
   - `DHAN_CLIENT_ID`
   - `DHAN_ACCESS_TOKEN`
3. TradingView alert capable of sending the `ticker`, `side`, and `price` fields
   (see [Sample payload](#sample-webhook)).

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Export your Dhan credentials before starting the server:

```bash
export DHAN_CLIENT_ID="your-client-id"
export DHAN_ACCESS_TOKEN="your-access-token"
```

Optional environment variables allow you to tweak the behaviour without editing
code:

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `INDEX_SYMBOL` | `NIFTY` | Underlying index symbol used for option chain lookups. |
| `STRIKE_STEP` | `50` | Strike increment used when rounding to the ATM strike. |
| `LOT_SIZE` | `50` | Fallback lot size if the option chain response omits it. |
| `OPTION_PRODUCT_TYPE` | `INTRADAY` | Dhan product type for orders. |
| `ORDER_VALIDITY` | `DAY` | Order validity sent to Dhan. |
| `TARGET_POINTS` | `10` | Target profit in points above the entry premium. |
| `ORDER_FILL_TIMEOUT` | `45` | Seconds to wait for an order to reach a terminal status before timing out. |
| `ORDER_POLL_INTERVAL` | `1.0` | Polling interval (seconds) while waiting for order status updates. |

## Running locally

```bash
uvicorn tradingview_delta_bot:app --host 0.0.0.0 --port 8000 --reload
```

### Sample webhook

```bash
curl -X POST http://127.0.0.1:8000/webhook \
  -H "Content-Type: application/json" \
  -d @sample_webhook.json
```

The bot will:

1. Resolve the nearest weekly expiry for NIFTY.
2. Download the Dhan option chain for that expiry.
3. Locate the ATM strike (rounded to the nearest 50 points).
4. Buy the corresponding call (`CE`) or put (`PE`) option depending on the
   signal side.
5. Submit a limit sell order 10 points above the entry premium.

If a subsequent alert in the opposite direction arrives before the target is
hit, the bot cancels the target order and squares off the existing position at
market.

## Deployment on cPanel

1. Copy the project files to the application directory on your cPanel host.
2. Create a Python application via **Setup Python App**, select the desired
   Python version, and point to the project directory.
3. Install dependencies using the virtualenv's `pip install -r requirements.txt`.
4. Add environment variables `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` under the
   application configuration.
5. Configure the startup command to use the provided `Procfile` or set a custom
   command such as `uvicorn tradingview_delta_bot:app --host 0.0.0.0 --port 8000`.
6. Expose port 8000 (or your chosen port) through cPanel's application routing
   so that TradingView webhook requests reach the FastAPI server.

### Preparing a downloadable bundle

If you prefer uploading a single archive to cPanel, create one from the project
root:

```bash
zip -r tradingview_dhan_bot.zip . -x "*.pyc" "__pycache__/*" ".git/*"
```

Upload the resulting `tradingview_dhan_bot.zip` through the cPanel file manager
and extract it in the target directory.

## Notes

- The bot currently supports only the NIFTY index.  To extend to other symbols,
  adjust `INDEX_SYMBOL`, `LOT_SIZE`, and the ticker validation logic.
- Ensure your TradingView alerts contain the latest underlying price; the bot
  uses this to determine the ATM strike.
- Review the DhanHQ API rate limits and account permissions before running the
  bot in production.
