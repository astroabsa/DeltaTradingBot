import os
import uvicorn
from fastapi import FastAPI, Request
from delta_client import DeltaClient

app = FastAPI()
client = DeltaClient(
    api_key=os.getenv("DELTA_API_KEY"),
    api_secret=os.getenv("DELTA_API_SECRET")
)

PRODUCT_MAP = {
    "SOLUSD.P": 175,  # Replace with actual product_id from get_products()
}
TARGET_MOVE = 0.50

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("Webhook received:", data)

    symbol = data.get("symbol")
    side = data.get("side")
    size = data.get("size", 1)

    if not symbol or symbol not in PRODUCT_MAP:
        return {"status": "error", "message": "Invalid or unsupported symbol"}

    product_id = PRODUCT_MAP[symbol]

    print(f"Cancelling all open orders for {symbol}")
    client.cancel_all_orders(product_id=product_id)

    _, positions_data = client.get_positions()
    existing_pos = None
    if "result" in positions_data:
        for pos in positions_data["result"]:
            if pos.get("product_id") == product_id and abs(float(pos.get("size", 0))) > 0:
                existing_pos = pos
                break

    if existing_pos:
        pos_side = existing_pos.get("side")
        pos_size = abs(float(existing_pos.get("size", 0)))
        if pos_side and pos_side != side:
            print(f"Closing opposite position {pos_side}, size {pos_size}")
            client.place_order(
                product_id=product_id,
                side="buy" if pos_side == "sell" else "sell",
                order_type="market_order",
                size=pos_size
            )

    r, resp = client.place_order(
        product_id=product_id,
        side=side,
        order_type="market_order",
        size=size
    )
    if r.status_code != 200 or "result" not in resp:
        return {"status": "error", "message": "Entry order failed", "resp": resp}

    order_info = resp["result"]
    entry_price = float(order_info.get("price", 0))
    print(f"Entry {side} at {entry_price}")

    if side == "buy":
        target_price = round(entry_price + TARGET_MOVE, 4)
        exit_side = "sell"
    else:
        target_price = round(entry_price - TARGET_MOVE, 4)
        exit_side = "buy"

    r2, resp2 = client.place_order(
        product_id=product_id,
        side=exit_side,
        order_type="limit_order",
        size=size,
        limit_price=str(target_price),
        time_in_force="gtc"
    )
    if r2.status_code != 200 or "result" not in resp2:
        return {"status": "error", "message": "Target order failed", "resp": resp2}

    print(f"Target {exit_side} at {target_price}")

    return {"status": "ok", "entry": entry_price, "target": target_price, "side": side}

if __name__ == "__main__":
    uvicorn.run("tradingview_delta_bot:app", host="0.0.0.0", port=8000, reload=True)
