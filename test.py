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
from apiKeys import APIKeys

# ====== Config (use env vars in prod) ======
apikeys = APIKeys()
API_KEY = apikeys.AccessAPIKey
API_SECRET = apikeys.SecretKey
PASSPHRASE = apikeys.Passphrase

BASE_URL = "https://api.bitget.com"

# Endpoints
PLACE_ORDER_PATH = "/api/v2/mix/order/place-order"
GET_ACCOUNT_PATH = "/api/v2/mix/account/account"
SET_POSMODE_PATH = "/api/v2/mix/account/set-position-mode"

DEFAULT_TIMEOUT = 15  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF_SECS = 1.5

# ====== Helpers ======
def get_timestamp() -> str:
    """Bitget expects milliseconds since epoch, as a string."""
    return str(int(time.time() * 1000))

def _sign(prehash: str, secret: str) -> str:
    # Encrypting the url that should be sent to Bitget to get it in the required format
    """HMAC-SHA256 + Base64 per Bitget v2 docs."""
    mac = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("ascii")

def _canonical_path(url_or_path: str) -> str:
    # Extracts u.path and u.query from url if it starts with 'http' → e.g. /api/v2/mix/order/place-order, symbol=BTCUSDT&size=1 else it already has the format
    """Bitget signing uses only the path+query (no scheme/host)."""
    u = urlparse(url_or_path)
    if u.scheme and u.netloc:
        return u.path + (f"?{u.query}" if u.query else "")
    path = url_or_path.strip()
    if not path.startswith("/"):
        path = "/" + path
    return path

def sign_request(timestamp: str, method: str, path_or_url: str, body_json: str) -> str:
    path = _canonical_path(path_or_url)
    prehash = f"{timestamp}{method.upper()}{path}{body_json}"
    return _sign(prehash, API_SECRET)

def _headers(signature: str, timestamp: str) -> Dict[str, str]:
    return {
        "ACCESS-KEY": API_KEY,
        "ACCESS-SIGN": signature,
        "ACCESS-PASSPHRASE": PASSPHRASE,
        "ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }

def _request_with_retries(method: str, url: str, headers: Dict[str, str], data: Optional[str] = None) -> requests.Response:
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=headers,
                data=data,
                timeout=DEFAULT_TIMEOUT,
            )
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECS * attempt)
                continue
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECS * attempt)
                continue
            raise
    if last_exc:
        raise last_exc

def _check_api_result(json_result: Dict[str, Any]) -> None:
    code = str(json_result.get("code"))
    if code != "00000":
        raise RuntimeError(f"Bitget API error: code={code}, msg={json_result.get('msg')}, data={json_result.get('data')}")

# ====== Signed HTTP helpers ======
# --- replace your _signed_get with this (adds better errors) ---
def _signed_get(path: str, params: Dict[str, str]) -> Dict[str, Any]:
    from urllib.parse import urlencode
    qs = urlencode(params, doseq=True)
    path_q = f"{path}?{qs}" if qs else path
    ts = get_timestamp()
    sig = sign_request(ts, "GET", path_q, "")
    hdrs = _headers(sig, ts)
    resp = _request_with_retries("GET", f"{BASE_URL}{path_q}", hdrs, None)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        # surface Bitget's error payload for debugging
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}") from e
    data = resp.json()
    _check_api_result(data)
    return data

def _signed_post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    ts = get_timestamp()
    body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    sig = sign_request(ts, "POST", path, body_json)
    hdrs = _headers(sig, ts)
    resp = _request_with_retries("POST", f"{BASE_URL}{path}", hdrs, body_json)
    resp.raise_for_status()
    data = resp.json()
    _check_api_result(data)
    return data

# ====== Position mode management (force hedge) ======
# Cache by productType to avoid repeated calls
_POSMODE_CACHE: Dict[str, str] = {}  # e.g., {"USDT-FUTURES": "hedge_mode"}

# --- force lowercase for the account endpoint ---
def get_position_mode(symbol: str, product_type: str, margin_coin: str) -> str:
    data = _signed_get(
        "/api/v2/mix/account/account",
        {
            "symbol": symbol.lower(),         # <-- lowercase required here
            "productType": product_type,      # e.g. "USDT-FUTURES"
            "marginCoin": margin_coin.lower() # <-- lowercase here too
        }
    )
    return data["data"]["posMode"]  # 'hedge_mode' or 'one_way_mode'


def set_position_mode_hedge(product_type: str) -> str:
    res = _signed_post(SET_POSMODE_PATH, {"productType": product_type, "posMode": "hedge_mode"})
    return res["data"]["posMode"]

def ensure_hedge_mode(symbol: str, product_type: str, margin_coin: str) -> None:
    cached = _POSMODE_CACHE.get(product_type)
    if cached == "hedge_mode":
        return
    current = get_position_mode(symbol, product_type, margin_coin)
    if current != "hedge_mode":
        # Will fail if there are open positions/orders in this productType.
        new_mode = set_position_mode_hedge(product_type)
        if new_mode != "hedge_mode":
            raise RuntimeError(f"Failed to set hedge mode: {new_mode}")
    _POSMODE_CACHE[product_type] = "hedge_mode"

# ====== Core order function (HEDGE MODE ONLY) ======
Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
ProductType = Literal["USDT-FUTURES", "USDC-FUTURES", "COIN-FUTURES"]
TradeSide = Literal["open", "close"]

def place_mix_order(
    symbol: str,
    size: str,
    side: Side,
    trade_side: TradeSide,              # REQUIRED in hedge mode
    order_type: OrderType = "market",
    *,
    product_type: ProductType = "USDT-FUTURES",
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
    ensure_hedge_mode(symbol, product_type, margin_coin)

    url = f"{BASE_URL}{PLACE_ORDER_PATH}"
    method = "POST"
    timestamp = get_timestamp()

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
    signature = sign_request(timestamp, method, PLACE_ORDER_PATH, body_json)
    headers = _headers(signature, timestamp)

    resp = _request_with_retries(method, url, headers, body_json)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}") from e

    result = resp.json()
    _check_api_result(result)
    return result

# ====== Hedge-mode convenience wrappers ======
def open_long_market(symbol: str, size: str, **kwargs) -> Dict[str, Any]:
    return place_mix_order(symbol, size, side="buy", trade_side="open", order_type="market", **kwargs)

def close_long_market(symbol: str, size: str, **kwargs) -> Dict[str, Any]:
    return place_mix_order(symbol, size, side="buy", trade_side="close", order_type="market", **kwargs)

def open_short_market(symbol: str, size: str, **kwargs) -> Dict[str, Any]:
    return place_mix_order(symbol, size, side="sell", trade_side="open", order_type="market", **kwargs)

def close_short_market(symbol: str, size: str, **kwargs) -> Dict[str, Any]:
    return place_mix_order(symbol, size, side="sell", trade_side="close", order_type="market", **kwargs)

def open_long_limit(symbol: str, size: str, price: str, **kwargs) -> Dict[str, Any]:
    return place_mix_order(symbol, size, side="buy", trade_side="open", order_type="limit", price=price, **kwargs)

def close_long_limit(symbol: str, size: str, price: str, **kwargs) -> Dict[str, Any]:
    return place_mix_order(symbol, size, side="buy", trade_side="close", order_type="limit", price=price, **kwargs)

def open_short_limit(symbol: str, size: str, price: str, **kwargs) -> Dict[str, Any]:
    return place_mix_order(symbol, size, side="sell", trade_side="open", order_type="limit", price=price, **kwargs)

def close_short_limit(symbol: str, size: str, price: str, **kwargs) -> Dict[str, Any]:
    return place_mix_order(symbol, size, side="sell", trade_side="close", order_type="limit", price=price, **kwargs)

# ====== Example usage (HEDGE MODE) ======
if __name__ == "__main__":
    try:
        # Open a small LONG (market) on SKLUSDT
        res = open_long_market("SKLUSDT", "127", product_type="USDT-FUTURES", margin_mode="isolated", margin_coin="USDT")
        print(json.dumps(res, indent=2))
        # Close that LONG (market)
        res2 = close_long_market("SKLUSDT", "127", product_type="USDT-FUTURES", margin_mode="isolated", margin_coin="USDT")
        print(json.dumps(res2, indent=2))
    except Exception as ex:
        print(f"Order failed: {ex}")

