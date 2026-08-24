import requests
import pandas as pd
import time
import os
import time
import json
import hmac
import uuid
import base64
import hashlib
from typing import Optional, Literal, Dict, Any, Tuple
from urllib.parse import urlparse, urlencode
import requests
from urllib.parse import urlencode

class BuySell:
    '''
    BuySell class to handle buy and sell operations for the trading bot.
    '''
    def __init__(self, container, apikeys):
        self.container = container
        self.apiKeys = apikeys

        self.API_KEY = self.apiKeys.AccessAPIKey
        self.API_SECRET = self.apiKeys.SecretKey
        self.PASSPHRASE = self.apiKeys.Passphrase

        self.BASE_URL = "https://api.bitget.com"

        self.PLACE_ORDER_PATH = "/api/v2/mix/order/place-order"
        self.GET_ACCOUNT_PATH = "/api/v2/mix/account/account"
        self.SET_POSMODE_PATH = "/api/v2/mix/account/set-position-mode"

        self.DEFAULT_TIMEOUT = 15  # seconds
        self.MAX_RETRIES = 3
        self.RETRY_BACKOFF_SECS = 1.5

        # ====== Position mode management (force hedge) ======
        # Cache by productType to avoid repeated calls
        self._POSMODE_CACHE: Dict[str, str] = {}  # e.g., {"USDT-FUTURES": "hedge_mode"}

    def get_timestamp(self) -> str:
        """Bitget expects milliseconds since epoch, as a string."""
        return str(int(time.time() * 1000))

    def _sign(self, prehash: str, secret: str) -> str:
        # Encrypting the url that should be sent to Bitget to get it in the required format
        """HMAC-SHA256 + Base64 per Bitget v2 docs."""
        mac = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode("ascii")

    def _canonical_path(self, url_or_path: str) -> str:
        # Extracts u.path and u.query from url if it starts with 'http' → e.g. /api/v2/mix/order/place-order, symbol=BTCUSDT&size=1 else it already has the format
        """Bitget signing uses only the path+query (no scheme/host)."""
        u = urlparse(url_or_path)
        if u.scheme and u.netloc:
            return u.path + (f"?{u.query}" if u.query else "")
        path = url_or_path.strip()
        if not path.startswith("/"):
            path = "/" + path
        return path

    def sign_request(self, timestamp: str, method: str, path_or_url: str, body_json: str) -> str:
        path = self._canonical_path(path_or_url)
        prehash = f"{timestamp}{method.upper()}{path}{body_json}"
        return self._sign(prehash, self.API_SECRET)

    def _headers(self, signature: str, timestamp: str) -> Dict[str, str]:
        return {
            "ACCESS-KEY": self.API_KEY,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": self.PASSPHRASE,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    def _request_with_retries(self, method: str, url: str, headers: Dict[str, str], data: Optional[str] = None) -> requests.Response:
        last_exc = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                resp = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=data,
                    timeout=self.DEFAULT_TIMEOUT,
                )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_BACKOFF_SECS * attempt)
                    continue
                return resp
            except requests.RequestException as e:
                last_exc = e
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_BACKOFF_SECS * attempt)
                    continue
                raise
        if last_exc:
            raise last_exc

    def _check_api_result(self, json_result: Dict[str, Any]) -> None:
        code = str(json_result.get("code"))
        if code != "00000":
            raise RuntimeError(f"Bitget API error: code={code}, msg={json_result.get('msg')}, data={json_result.get('data')}")

    def _signed_get(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        qs = urlencode(params, doseq=True)
        path_q = f"{path}?{qs}" if qs else path
        ts = self.get_timestamp()
        sig = self.sign_request(ts, "GET", path_q, "")
        hdrs = self._headers(sig, ts)
        resp = self._request_with_retries("GET", f"{self.BASE_URL}{path_q}", hdrs, None)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            # surface Bitget's error payload for debugging
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}") from e
        data = resp.json()
        self._check_api_result(data)
        return data

    def _signed_post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        ts = self.get_timestamp()
        body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        sig = self.sign_request(ts, "POST", path, body_json)
        hdrs = self._headers(sig, ts)
        resp = self._request_with_retries("POST", f"{self.BASE_URL}{path}", hdrs, body_json)
        resp.raise_for_status()
        data = resp.json()
        self._check_api_result(data)
        return data

    def get_position_mode(self, symbol: str, product_type: str, margin_coin: str) -> str:
        data = self._signed_get(
            "/api/v2/mix/account/account",
            {
                "symbol": symbol.lower(),         # <-- lowercase required here
                "productType": product_type,      # e.g. "USDT-FUTURES"
                "marginCoin": margin_coin.lower() # <-- lowercase here too
            }
        )
        return data["data"]["posMode"]  # 'hedge_mode' or 'one_way_mode'

    def set_position_mode_hedge(self, product_type: str) -> str:
        res = self._signed_post(self.SET_POSMODE_PATH, {"productType": product_type, "posMode": "hedge_mode"})
        return res["data"]["posMode"]

    def ensure_hedge_mode(self, symbol: str, product_type: str, margin_coin: str) -> None:
        cached = self._POSMODE_CACHE.get(product_type)
        if cached == "hedge_mode":
            return
        current = self.get_position_mode(symbol, product_type, margin_coin)
        if current != "hedge_mode":
            # Will fail if there are open positions/orders in this productType.
            new_mode = self.set_position_mode_hedge(product_type)
            if new_mode != "hedge_mode":
                raise RuntimeError(f"Failed to set hedge mode: {new_mode}")
        self._POSMODE_CACHE[product_type] = "hedge_mode"

    def place_mix_order(
        self,
        symbol: str,
        size: str,
        side: Literal["buy", "sell"],
        trade_side: Literal["open", "close"],  # REQUIRED in hedge mode
        order_type: Literal["market", "limit"] = "market",
        *,
        product_type: Literal["USDT-FUTURES", "USDC-FUTURES", "COIN-FUTURES"] = "USDT-FUTURES",
        margin_mode: Literal["isolated", "crossed"] = "isolated",
        margin_coin: str = "USDT",
        price: Optional[str] = None,
        client_oid: Optional[str] = None,
        preset_take_profit_price: Optional[str] = None,       # presetStopSurplusPrice
        preset_stop_loss_price: Optional[str] = None,         # presetStopLossPrice
        preset_tp_exec_price: Optional[str] = None,           # presetStopSurplusExecutePrice
        preset_sl_exec_price: Optional[str] = None,           # presetStopLossExecutePrice
        force: Optional[Literal["gtc","ioc","fok","post_only"]] = None,
    ) -> Dict[str, Any]:
        """
        Place a MIX (futures) order on Bitget in **hedge mode**.
        You MUST specify trade_side = "open" or "close".
        """
        if order_type == "limit" and not price:
            raise ValueError("price is required for limit orders")

        # Ensure account is in hedge mode for this productType
        self.ensure_hedge_mode(symbol, product_type, margin_coin)

        url = f"{self.BASE_URL}{self.PLACE_ORDER_PATH}"
        method = "POST"
        timestamp = self.get_timestamp()

        body: Dict[str, Any] = {
            "symbol": symbol,
            "productType": product_type,
            "marginMode": margin_mode,
            "marginCoin": margin_coin,
            "size": size,
            "side": side,               # buy = long dir, sell = short dir
            "tradeSide": trade_side,    # open | close (REQUIRED in hedge)
            "orderType": order_type,
            "clientOid": client_oid or uuid.uuid4().hex,
        }
        if price is not None:
            body["price"] = price
        if force is not None:
            body["force"] = force

        # TP/SL (Bitget field names)
        if preset_take_profit_price is not None:
            body["presetStopSurplusPrice"] = preset_take_profit_price
        if preset_stop_loss_price is not None:
            body["presetStopLossPrice"] = preset_stop_loss_price
        if preset_tp_exec_price is not None:
            body["presetStopSurplusExecutePrice"] = preset_tp_exec_price
        if preset_sl_exec_price is not None:
            body["presetStopLossExecutePrice"] = preset_sl_exec_price

        body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        signature = self.sign_request(timestamp, method, self.PLACE_ORDER_PATH, body_json)
        headers = self._headers(signature, timestamp)

        resp = self._request_with_retries(method, url, headers, body_json)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}") from e

        result = resp.json()
        self._check_api_result(result)
        return result
    
    def open_long_market(self, symbol: str, size: str, **kwargs) -> Dict[str, Any]:
        return self.place_mix_order(symbol, size, side="buy", trade_side="open", order_type="market", **kwargs)

    def close_long_market(self, symbol: str, size: str, **kwargs) -> Dict[str, Any]:
        return self.place_mix_order(symbol, size, side="buy", trade_side="close", order_type="market", **kwargs)

    def open_short_market(self, symbol: str, size: str, **kwargs) -> Dict[str, Any]:
        return self.place_mix_order(symbol, size, side="sell", trade_side="open", order_type="market", **kwargs)

    def close_short_market(self, symbol: str, size: str, **kwargs) -> Dict[str, Any]:
        return self.place_mix_order(symbol, size, side="sell", trade_side="close", order_type="market", **kwargs)
    
    # ---------- SPOT order helpers ----------
    def place_spot_order(
        self,
        symbol: str,
        size: str,
        side: Literal["buy", "sell"],
        order_type: Literal["market", "limit"] = "market",
        *,
        price: Optional[str] = None,
        client_oid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Place a spot order. Uses Bitget spot create-order endpoint.

        Note: Bitget's exact field names may vary; this helper uses the common
        fields: `symbol`, `side`, `type`, `price`, `size`, `clientOid`.
        If Bitget returns an error about invalid parameters, adapt the body
        to the exact API contract (e.g., use `quantity` instead of `size`).
        """
        if order_type == "limit" and not price:
            raise ValueError("price is required for limit orders")

        path = "/api/spot/v1/trade/orders"
        body: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "size": size,
            "clientOid": client_oid or uuid.uuid4().hex,
        }
        if price is not None:
            body["price"] = price

        return self._signed_post(path, body)

    def buy_spot_market(self, symbol: str, size: str, **kwargs) -> Dict[str, Any]:
        return self.place_spot_order(symbol, size, side="buy", order_type="market", **kwargs)

    def sell_spot_market(self, symbol: str, size: str, **kwargs) -> Dict[str, Any]:
        return self.place_spot_order(symbol, size, side="sell", order_type="market", **kwargs)

    def buy_spot_limit(self, symbol: str, size: str, price: str, **kwargs) -> Dict[str, Any]:
        return self.place_spot_order(symbol, size, side="buy", order_type="limit", price=price, **kwargs)

    def sell_spot_limit(self, symbol: str, size: str, price: str, **kwargs) -> Dict[str, Any]:
        return self.place_spot_order(symbol, size, side="sell", order_type="limit", price=price, **kwargs)
    
