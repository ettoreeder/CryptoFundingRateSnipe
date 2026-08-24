import numpy as np
import matplotlib.pyplot as plt
from importer import Importer
from container import Container
from buySell import BuySell
from apiKeys import APIKeys
from tqdm import tqdm
import json
import time


class Main():
    def __init__(self):
        self.container = Container()
        self.apikeys = APIKeys()
        self.importer = Importer(self.container)
        self.buy_sell = BuySell(self.container, self.apikeys)

    def testingfundingRateImports(self):
        for key, value in self.importer.importTargetFundingRatesSafe(0.005, self.buy_sell).items():
            # print(f"Symbol: {key}, Funding Rate: {value['fundingRate']}, USDT Volume: {value['usdtVolume']}, Timestamp: {value['ts']}")
            print(key, value)

    def testing_all(self):
        # print(self.importer.fetch_bitget_funding_rates('BTCUSDT'))
        print(self.importer.importTargetFundingRatesSafe(0.001, self.buy_sell))

if __name__ == "__main__":
    main = Main()
    
    # main.testing_all()
    
    main.testingfundingRateImports()
    # # Open a small LONG (market) on HUSDT
    # res = main.buy_sell.open_long_market("HUSDT", "13", product_type="USDT-FUTURES", margin_mode="isolated", margin_coin="USDT")
    # print(json.dumps(res, indent=2))
    # time.sleep(5)
    # # Close that LONG (market)
    # res2 = main.buy_sell.close_long_market("HUSDT", "13", product_type="USDT-FUTURES", margin_mode="isolated", margin_coin="USDT")
    # print(json.dumps(res2, indent=2))

    # spot orders
    res = main.buy_sell.buy_spot_market("HUSDT", "13")
    print(json.dumps(res, indent=2))
    time.sleep(5)
    # Close that LONG (market)
    res2 = main.buy_sell.close_long_market("HUSDT", "13", product_type="USDT-FUTURES", margin_mode="isolated", margin_coin="USDT")
    print(json.dumps(res2, indent=2))
