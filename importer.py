import requests
import pandas as pd
import time
from typing import Dict, Any, Optional

class Importer:
    '''
    Imports Bitget funding data and filters to symbols that are tradable
    with *your* API (auth/IP ok, symbol allows API trading, size rules known).
    '''
    def __init__(self, container):
        self.container = container
        self.BASE = 'https://api.bitget.com'
        self.TIMEOUT = 12

    # ---------- existing ----------
    def fetch_bitget_funding_rates(self, symbol: str) -> dict:
        url = f'{self.BASE}/api/v2/mix/market/current-fund-rate'
        params = {'symbol': symbol, 'productType': 'USDT-FUTURES'}  # docs also show lowercase accepted
        r = requests.get(url, params=params, timeout=self.TIMEOUT)
        r.raise_for_status()
        return r.json()

    def importTargetFundingRates(self, threshold: float) -> dict:
        requestFundingRates = requests.get(
            f'{self.BASE}/api/v2/mix/market/tickers',
            params={'productType': 'USDT-FUTURES'},
            timeout=self.TIMEOUT
        )
        dataFundingRates = requestFundingRates.json().get('data', [])

        dfFundingRates = pd.DataFrame(dataFundingRates)
        if dfFundingRates.empty:
            return {}
        dfFundingRates['fundingRate'] = dfFundingRates['fundingRate'].astype(float)

        filtered = dfFundingRates[dfFundingRates['fundingRate'].abs() >= threshold]

        targetFundingRates = {}
        for _, row in filtered.iterrows():
            req = requests.get(
                f'{self.BASE}/api/v2/mix/market/funding-time',
                params={'productType': 'USDT-FUTURES', 'symbol': row['symbol']},
                timeout=self.TIMEOUT
            )
            nxt = pd.to_datetime(int(req.json().get('data')[0]['nextFundingTime']), unit='ms', utc=True)
            targetFundingRates[row['symbol']] = {
                'fundingRate': row['fundingRate'],
                'usdtVolume': row['usdtVolume'],
                'ts': nxt
            }
        return targetFundingRates

    # ---------- NEW: public contract info ----------
    def fetch_contract_info(self, symbol: str, product_type: str = 'USDT-FUTURES') -> Optional[Dict[str, Any]]:
        """
        Get Bitget contract config for a symbol (minTradeNum, sizeMultiplier, symbolStatus, etc.)
        Returns the first (and only) entry or None if not found.
        """
        params = {'productType': product_type.lower(), 'symbol': symbol.upper()}
        r = requests.get(f'{self.BASE}/api/v2/mix/market/contracts', params=params, timeout=self.TIMEOUT)
        r.raise_for_status()
        j = r.json()
        data = j.get('data') or []
        return data[0] if data else None

    # ---------- NEW: private pre-trade check ----------
    def can_trade_with_my_api(self, buy_sell, product_type: str = 'USDT-FUTURES') -> (bool, str):
        """
        Quick authenticated check: proves your key/IP/permissions are valid for this product line.
        Returns (ok, reason_if_not_ok).
        """
        try:
            _ = buy_sell._signed_get('/api/v2/mix/account/accounts', {'productType': product_type})
            return True, ''
        except Exception as e:
            return False, str(e)

    # ---------- NEW: filter targets to only-tradable ----------
    def importTargetFundingRatesSafe(self, threshold: float, buy_sell, margin_coin: str = 'USDT') -> dict:
        """
        Like importTargetFundingRates, but keep only symbols that:
          - exist in contract config,
          - have symbolStatus in {'normal','listed'} and not 'restrictedAPI',
          - support the requested margin coin,
          - and your API auth/IP is valid for the product line.
        """
        # 1) Auth/IP preflight once
        ok, reason = self.can_trade_with_my_api(buy_sell, product_type='USDT-FUTURES')
        if not ok:
            raise RuntimeError(f"API not ready for trading (auth/IP/perm): {reason}")

        # 2) Get funding targets as before
        targets = self.importTargetFundingRates(threshold)
        safe: Dict[str, Dict[str, Any]] = {}

        # 3) Keep only tradable symbols
        for sym, info in targets.items():
            ci = self.fetch_contract_info(sym, product_type='USDT-FUTURES')
            if not ci:
                continue  # unknown symbol in contracts list

            status = (ci.get('symbolStatus') or '').lower()
            if status not in ('normal', 'listed'):
                continue
            if status == 'restrictedapi':
                continue  # API trading disabled for this symbol

            # Must support desired margin coin (e.g., USDT)
            supports = set((ci.get('supportMarginCoins') or []))
            if margin_coin.upper() not in supports:
                continue

            # Provide sizing helpers for your order layer
            safe[sym] = {
                **info,
                'minTradeNum': ci.get('minTradeNum'),
                'sizeMultiplier': ci.get('sizeMultiplier'),
                'pricePlace': ci.get('pricePlace'),
                'volumePlace': ci.get('volumePlace'),
                'maxMarketOrderQty': ci.get('maxMarketOrderQty'),
                'symbolStatus': ci.get('symbolStatus'),
            }
        return safe
