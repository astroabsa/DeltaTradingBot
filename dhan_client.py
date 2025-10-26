"""Dhan API client helpers for trading automation.

This module contains a lightweight REST client that only implements the
endpoints required by the trading bot.  The implementation follows the v2 API
reference available at https://dhanhq.co/docs/v2/.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import requests

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.dhan.co"


class DhanClientError(RuntimeError):
    """Raised when the Dhan API returns a non-success response."""


@dataclass
class DhanConfig:
    """Configuration required to talk to Dhan APIs."""

    client_id: str
    access_token: str
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls) -> "DhanConfig":
        client_id = os.getenv("DHAN_CLIENT_ID")
        access_token = os.getenv("DHAN_ACCESS_TOKEN")
        if not client_id or not access_token:
            raise RuntimeError(
                "DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN environment variables must be set"
            )
        return cls(client_id=client_id, access_token=access_token)


class DhanClient:
    """Simple synchronous client for the Dhan trading APIs."""

    def __init__(self, config: Optional[DhanConfig] = None):
        self.config = config or DhanConfig.from_env()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Client-Id": self.config.client_id,
                "access-token": self.config.access_token,
            }
        )

    # ------------------------------------------------------------------
    # Low level helpers
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: tuple[int, int] = (5, 30),
    ) -> Dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        LOGGER.debug("Dhan request %s %s params=%s json=%s", method, url, params, json)
        response = self.session.request(
            method,
            url,
            params=params,
            json=json,
            timeout=timeout,
        )
        try:
            payload = response.json()
        except ValueError as exc:  # pragma: no cover - defensive logging
            LOGGER.error("Invalid JSON response from %s: %s", url, response.text)
            raise DhanClientError("Invalid JSON from Dhan API") from exc

        if not response.ok:
            LOGGER.error(
                "Dhan API error %s %s: status=%s payload=%s",
                method,
                path,
                response.status_code,
                payload,
            )
            raise DhanClientError(payload)
        return payload

    # ------------------------------------------------------------------
    # Market data helpers
    # ------------------------------------------------------------------
    def get_option_chain(
        self,
        index_symbol: str,
        expiry: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch the option chain for the provided index symbol.

        Parameters
        ----------
        index_symbol:
            Valid symbols are ``NIFTY``, ``BANKNIFTY``, etc., as documented by
            Dhan.
        expiry:
            Optional ISO formatted expiry date (``YYYY-MM-DD``).  When omitted,
            the API returns the nearest expiry for each strike.
        """

        params: Dict[str, Any] = {"indexSymbol": index_symbol.upper()}
        if expiry:
            params["expiry"] = expiry
        payload = self._request("GET", "/v2/options/chain", params=params)
        # API responds with {"data": [...]} as per documentation.
        return payload.get("data", [])

    def get_market_quotes(self, security_ids: Iterable[str]) -> Dict[str, Any]:
        """Fetch market quotes for one or more security IDs."""

        body = {"securityIds": list(security_ids)}
        payload = self._request("POST", "/v2/market/quotes", json=body)
        return payload.get("data", {})

    # ------------------------------------------------------------------
    # Order management helpers
    # ------------------------------------------------------------------
    def place_order(
        self,
        *,
        security_id: str,
        transaction_type: str,
        quantity: int,
        order_type: str,
        product_type: str,
        validity: str,
        price: Optional[float] = None,
        disclosed_quantity: int = 0,
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Place an order with Dhan.

        ``transaction_type`` must be ``BUY`` or ``SELL``.  ``order_type`` is one
        of ``MARKET`` or ``LIMIT``.  ``product_type`` is typically ``INTRADAY``
        or ``CNC`` for option buying.  ``validity`` can be ``DAY`` or ``IOC``.
        """

        order: Dict[str, Any] = {
            "securityId": str(security_id),
            "transactionType": transaction_type.upper(),
            "quantity": int(quantity),
            "orderType": order_type.upper(),
            "productType": product_type.upper(),
            "validity": validity.upper(),
            "disclosedQuantity": int(disclosed_quantity),
        }
        if price is not None:
            order["price"] = float(price)
        if tag:
            order["tag"] = tag

        payload = self._request("POST", "/v2/orders", json=order)
        return payload.get("data", payload)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel a pending order."""

        return self._request("DELETE", f"/v2/orders/{order_id}")

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Retrieve order status."""

        payload = self._request("GET", f"/v2/orders/{order_id}")
        return payload.get("data", payload)


def get_quote_ltp(quotes: Dict[str, Any], security_id: str) -> Optional[float]:
    """Helper to extract the last traded price from quote payload."""

    data = quotes.get(str(security_id))
    if not data:
        return None
    # Field names follow the Dhan API payload structure.
    ltp = data.get("lastTradedPrice")
    if ltp is None:
        ltp = data.get("ltp")
    if ltp is None:
        return None
    return float(ltp)
