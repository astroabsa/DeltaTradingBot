from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from dhan_client import DhanClient, DhanClientError, get_quote_ltp

LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="TradingView → Dhan Bridge")

dhan_client = DhanClient()

INDEX_SYMBOL = os.getenv("INDEX_SYMBOL", "NIFTY").upper()
OPTION_PRODUCT_TYPE = os.getenv("OPTION_PRODUCT_TYPE", "INTRADAY").upper()
ORDER_VALIDITY = os.getenv("ORDER_VALIDITY", "DAY").upper()
DEFAULT_LOT_SIZE = int(os.getenv("LOT_SIZE", "50"))
TARGET_POINTS = float(os.getenv("TARGET_POINTS", "10"))
STRIKE_STEP = int(os.getenv("STRIKE_STEP", "50"))
ORDER_FILL_TIMEOUT = float(os.getenv("ORDER_FILL_TIMEOUT", "45"))
ORDER_POLL_INTERVAL = float(os.getenv("ORDER_POLL_INTERVAL", "1.0"))


class TradingViewSignal(BaseModel):
    ticker: str = Field(..., description="TradingView ticker that generated the signal")
    side: str = Field(..., regex=r"^(?i)(buy|sell)$")
    price: float = Field(..., gt=0, description="Underlying price from TradingView")
    quantity: Optional[int] = Field(
        default=1,
        gt=0,
        description="Number of option lots to trade (defaults to one lot)",
    )


class ActiveTrade(BaseModel):
    security_id: str
    option_type: str
    strike: int
    expiry: date
    direction: str
    quantity: int
    lot_size: int
    entry_price: float
    entry_order_id: str
    target_order_id: Optional[str] = None


class OptionTradeManager:
    def __init__(self) -> None:
        self.active: Optional[ActiveTrade] = None
        self._lock = asyncio.Lock()

    async def handle_signal(self, payload: TradingViewSignal) -> Dict[str, Optional[str]]:
        async with self._lock:
            side = payload.side.lower()
            option_type = "CE" if side == "buy" else "PE"
            direction = "CALL" if option_type == "CE" else "PUT"

            # Determine expiry and ATM strike.
            expiry = get_next_expiry(date.today())
            strike = compute_atm_strike(payload.price)

            option = await lookup_option_security(
                index_symbol=INDEX_SYMBOL,
                expiry=expiry,
                strike=strike,
                option_type=option_type,
            )
            security_id = option["securityId"]
            lot_size = get_option_lot_size(option)

            lot_quantity = payload.quantity or 1
            quantity = lot_quantity * lot_size

            LOGGER.info(
                "Signal %s | option=%s strike=%s expiry=%s lots=%s lot_size=%s",
                direction,
                security_id,
                strike,
                expiry,
                lot_quantity,
                lot_size,
            )

            if self.active and self.active.direction == direction:
                LOGGER.info("Already have an open %s position, ignoring duplicate signal", direction)
                return {"status": "ignored", "reason": "position_already_open"}

            if self.active and self.active.direction != direction:
                LOGGER.info("Opposite signal detected, flattening existing position before entering new trade")
                await self._close_position(reason="reverse_signal")

            # Fetch current premium to derive target exit price.
            ltp = await fetch_option_ltp(security_id)
            if ltp is None:
                raise HTTPException(status_code=502, detail="Unable to fetch option quote from Dhan")

            entry_response = await place_market_order(
                security_id=security_id,
                quantity=quantity,
                transaction_type="BUY",
                tag=f"tv-entry-{datetime.utcnow().isoformat()}",
            )
            entry_order_id = extract_order_id(entry_response)
            if not entry_order_id:
                raise HTTPException(status_code=502, detail="Entry order response missing order id")

            try:
                entry_fill = await wait_for_order_completion(entry_order_id)
            except TimeoutError:
                LOGGER.warning("Timed out while waiting for entry order %s to complete", entry_order_id)
                entry_fill = entry_response

            status = normalize_order_status(entry_fill)
            if status and status in TERMINAL_FAILURE_STATUSES:
                raise HTTPException(status_code=502, detail=f"Entry order {status.lower()}")

            entry_price = determine_execution_price(entry_fill, fallback_price=ltp)
            target_price = round(entry_price + TARGET_POINTS, 2)

            target_response = await place_limit_exit(
                security_id=security_id,
                quantity=quantity,
                price=target_price,
                tag=f"tv-target-{entry_order_id}",
            )
            target_order_id = extract_order_id(target_response)

            self.active = ActiveTrade(
                security_id=str(security_id),
                option_type=option_type,
                strike=strike,
                expiry=expiry,
                direction=direction,
                quantity=quantity,
                lot_size=lot_size,
                entry_price=entry_price,
                entry_order_id=str(entry_order_id),
                target_order_id=str(target_order_id) if target_order_id else None,
            )

            return {
                "status": "entered",
                "security_id": self.active.security_id,
                "strike": str(self.active.strike),
                "expiry": self.active.expiry.isoformat(),
                "target_price": str(target_price),
                "entry_order_id": self.active.entry_order_id,
                "target_order_id": self.active.target_order_id,
            }

    async def _close_position(self, reason: str) -> None:
        if not self.active:
            return

        LOGGER.info("Closing %s position due to %s", self.active.direction, reason)
        if self.active.target_order_id:
            try:
                await cancel_order_async(self.active.target_order_id)
            except HTTPException as exc:
                LOGGER.warning(
                    "Failed to cancel target order %s: %s",
                    self.active.target_order_id,
                    exc.detail,
                )

        try:
            exit_response = await place_market_order(
                security_id=self.active.security_id,
                quantity=self.active.quantity,
                transaction_type="SELL",
                tag=f"tv-exit-{reason}",
            )
            exit_order_id = extract_order_id(exit_response)
            if exit_order_id:
                try:
                    exit_fill = await wait_for_order_completion(exit_order_id)
                    status = normalize_order_status(exit_fill)
                    if status and status in TERMINAL_FAILURE_STATUSES:
                        LOGGER.error("Exit order %s ended with status %s", exit_order_id, status)
                except TimeoutError:
                    LOGGER.warning("Timed out waiting for exit order %s to complete", exit_order_id)
        finally:
            self.active = None

    async def exit_on_command(self) -> Dict[str, str]:
        async with self._lock:
            if not self.active:
                raise HTTPException(status_code=400, detail="No open position")
            await self._close_position(reason="manual_exit")
            return {"status": "exited"}


manager = OptionTradeManager()


def compute_atm_strike(spot_price: float, strike_step: int = STRIKE_STEP) -> int:
    if strike_step <= 0:
        raise HTTPException(status_code=500, detail="Invalid strike step configuration")
    return int(round(spot_price / strike_step) * strike_step)


def get_next_expiry(today: date) -> date:
    # Weekly NIFTY options expire on Thursday (weekday=3).
    days_ahead = (3 - today.weekday()) % 7
    if days_ahead == 0 and datetime.utcnow().time().hour >= 15:
        # If it is expiry day but post market close, take next week.
        days_ahead = 7
    return today + timedelta(days=days_ahead)


async def lookup_option_security(
    *, index_symbol: str, expiry: date, strike: int, option_type: str
) -> Dict[str, Any]:
    option_chain = await asyncio.to_thread(
        dhan_client.get_option_chain,
        index_symbol,
        expiry.isoformat(),
    )
    for option in option_chain:
        option_expiry = str(option.get("expiry", ""))[:10]
        option_strike = option.get("strikePrice") or option.get("strike")
        if option_strike is None:
            continue
        if (
            str(option.get("optionType", "")).upper() == option_type
            and int(float(option_strike)) == strike
            and option_expiry == expiry.isoformat()
        ):
            return option
    raise HTTPException(status_code=404, detail="Unable to locate option contract in Dhan option chain")


async def place_market_order(
    *, security_id: str, quantity: int, transaction_type: str, tag: Optional[str]
) -> Dict[str, Any]:
    try:
        response = await asyncio.to_thread(
            dhan_client.place_order,
            security_id,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type="MARKET",
            product_type=OPTION_PRODUCT_TYPE,
            validity=ORDER_VALIDITY,
            tag=tag,
        )
        return response
    except DhanClientError as exc:
        LOGGER.exception("Failed to place market order")
        raise HTTPException(status_code=502, detail=str(exc))


async def place_limit_exit(
    *, security_id: str, quantity: int, price: float, tag: Optional[str]
) -> Dict[str, Any]:
    try:
        response = await asyncio.to_thread(
            dhan_client.place_order,
            security_id,
            transaction_type="SELL",
            quantity=quantity,
            order_type="LIMIT",
            product_type=OPTION_PRODUCT_TYPE,
            validity=ORDER_VALIDITY,
            price=price,
            tag=tag,
        )
        return response
    except DhanClientError as exc:
        LOGGER.exception("Failed to place target order")
        raise HTTPException(status_code=502, detail=str(exc))


async def cancel_order_async(order_id: str) -> None:
    try:
        await asyncio.to_thread(dhan_client.cancel_order, order_id)
    except DhanClientError as exc:
        LOGGER.warning("Failed to cancel order %s: %s", order_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))


async def fetch_option_ltp(security_id: str) -> Optional[float]:
    quotes = await asyncio.to_thread(dhan_client.get_market_quotes, [security_id])
    return get_quote_ltp(quotes, security_id)


def get_option_lot_size(option: Dict[str, Any]) -> int:
    for key in ("lotSize", "lotSizeValue", "lot"):
        value = option.get(key)
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return DEFAULT_LOT_SIZE


def extract_order_id(order: Dict[str, Any]) -> Optional[str]:
    for key in ("orderId", "id", "dhanOrderId", "order_id"):
        value = order.get(key)
        if value:
            return str(value)
    return None


def determine_execution_price(order: Dict[str, Any], *, fallback_price: float) -> float:
    for key in ("averagePrice", "avgPrice", "price", "tradedPrice"):
        value = order.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return float(fallback_price)


def normalize_order_status(order: Dict[str, Any]) -> str:
    status = order.get("orderStatus") or order.get("status") or order.get("order_state")
    if not status:
        return ""
    return str(status).upper()


TERMINAL_SUCCESS_STATUSES = {"TRADED", "COMPLETED", "EXECUTED", "FILLED", "COMPLETE"}
TERMINAL_FAILURE_STATUSES = {"REJECTED", "CANCELLED", "CANCELED", "FAILED", "EXPIRED"}


async def wait_for_order_completion(order_id: str) -> Dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + ORDER_FILL_TIMEOUT
    while True:
        snapshot = await asyncio.to_thread(dhan_client.get_order, order_id)
        status = normalize_order_status(snapshot)
        if status in TERMINAL_SUCCESS_STATUSES or status in TERMINAL_FAILURE_STATUSES:
            return snapshot
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"Timed out waiting for order {order_id} to complete")
        await asyncio.sleep(ORDER_POLL_INTERVAL)


@app.post("/webhook")
async def tradingview_webhook(payload: TradingViewSignal):
    """Entry point for TradingView webhook alerts."""

    if payload.ticker.upper() not in {"NIFTY", "NSE:NIFTY", "NIFTY50"}:
        raise HTTPException(status_code=400, detail="This bot is configured for NIFTY only")

    result = await manager.handle_signal(payload)
    return result


@app.post("/exit")
async def manual_exit():
    """Manual endpoint to flatten the position from a webhook or UI."""

    result = await manager.exit_on_command()
    return result


@app.get("/health")
def healthcheck():
    return {"status": "ok", "active_trade": manager.active.dict() if manager.active else None}


if __name__ == "__main__":
    uvicorn.run("tradingview_delta_bot:app", host="0.0.0.0", port=8000)
