import os
import time
import hmac
import hashlib
import json
from urllib.parse import urlencode
import requests

DEFAULT_BASE = "https://api.india.delta.exchange"

class DeltaClient:
    def __init__(self, api_key=None, api_secret=None, base_url=DEFAULT_BASE, user_agent="python-rest-client"):
        self.api_key = api_key or os.getenv("DELTA_API_KEY")
        self.api_secret = api_secret or os.getenv("DELTA_API_SECRET")
        if not self.api_key or not self.api_secret:
            raise ValueError("API key and secret required (env DELTA_API_KEY, DELTA_API_SECRET or pass into constructor)")
        self.base_url = base_url.rstrip("")
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

    def _generate_signature(self, method: str, path: str, query_string: str, body: str, timestamp: str):
        prehash = f"{method}{timestamp}{path}{query_string}{body}"
        mac = hmac.new(self.api_secret.encode('utf-8'), prehash.encode('utf-8'), hashlib.sha256)
        return mac.hexdigest()

    def _prepare_request(self, method: str, path: str, params: dict = None, body_obj: dict = None, timeout=(5, 30)):
        method = method.upper()
        params = params or {}
        query_string = ''
        if params:
            query_string = '?' + urlencode(params, doseq=True)
        body = ''
        if body_obj is not None:
            body = json.dumps(body_obj, separators=(',', ':'))
        timestamp = str(int(time.time()))
        signature = self._generate_signature(method, path, query_string, body, timestamp)
        headers = {
            "api-key": self.api_key,
            "timestamp": timestamp,
            "signature": signature,
        }
        url = self.base_url + path
        response = self.session.request(method, url, params=params, data=body if body else None,
                                        headers=headers, timeout=timeout)
        try:
            return response, response.json()
        except ValueError:
            return response, {"success": False, "raw_text": response.text}

    def get_products(self, product_type: str = None, page_size: int = 100):
        path = "/v2/products"
        params = {"page_size": page_size}
        if product_type:
            params["product_type"] = product_type
        return self._prepare_request("GET", path, params=params)

    def get_positions(self):
        path = "/v2/positions"
        return self._prepare_request("GET", path)

    def cancel_all_orders(self, product_id: int = None, contract_types: list = None):
        path = "/v2/orders/all"
        body = {}
        if product_id is not None:
            body["product_id"] = int(product_id)
        if contract_types is not None:
            body["contract_types"] = contract_types
        return self._prepare_request("DELETE", path, params={}, body_obj=body)

    def place_order(self, product_id: int = None, product_symbol: str = None,
                    side: str = "buy", order_type: str = "limit_order",
                    size=None, limit_price=None, time_in_force: str = "gtc",
                    client_order_id: str = None, stop_price: str = None,
                    mmp: str = "disabled", post_only: bool = False):
        path = "/v2/orders"
        body = {"order_type": order_type, "side": side}
        if product_id is not None:
            body["product_id"] = int(product_id)
        elif product_symbol is not None:
            body["product_symbol"] = str(product_symbol)
        else:
            raise ValueError("Either product_id or product_symbol must be provided")
        if size is not None:
            body["size"] = size
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if time_in_force is not None:
            body["time_in_force"] = time_in_force
        if client_order_id is not None:
            body["client_order_id"] = client_order_id
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if mmp is not None:
            body["mmp"] = mmp
        if post_only:
            body["post_only"] = True
        return self._prepare_request("POST", path, params={}, body_obj=body)
