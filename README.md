# Delta Exchange Trading Bot

This bot connects TradingView signals to Delta Exchange India and executes trades automatically with a fixed +0.50 target.

## Files

- **delta_client.py** → Delta Exchange API client
- **tradingview_delta_bot.py** → FastAPI server to listen to TradingView webhooks
- **requirements.txt** → Dependencies
- **sample_webhook.json** → Example TradingView webhook payload for testing

## Setup

1. Clone or unzip this package.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Export your API keys (from Delta Exchange):
   ```bash
   export DELTA_API_KEY="your_api_key"
   export DELTA_API_SECRET="your_api_secret"
   ```

## Run the Bot

Start the FastAPI server:
```bash
uvicorn tradingview_delta_bot:app --reload --host 0.0.0.0 --port 8000
```

## Test with Sample Webhook

Send the sample JSON to the bot:
```bash
curl -X POST http://127.0.0.1:8000/webhook \
-H "Content-Type: application/json" \
-d @sample_webhook.json
```

If successful, the bot will:
- Cancel open orders for SOLUSD.P
- Place a market entry order (buy/sell)
- Place a limit exit order at entry ± 0.50

## Notes

- Currently only **SOLUSD.P** is supported (update `PRODUCT_MAP` in `tradingview_delta_bot.py` if needed).
- Default target: **0.50** price move.
- No stop-loss included (only fixed target).
