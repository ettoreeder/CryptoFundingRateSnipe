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
        for key, value in self.importer.importTargetFundingRatesSafe(0.01, self.buy_sell).items():
            print(f"Symbol: {key}, Funding Rate: {value['fundingRate']}, USDT Volume: {value['usdtVolume']}, Timestamp: {value['ts']}")

if __name__ == "__main__":
    main = Main()
    main.testingfundingRateImports()
    # Open a small LONG (market) on SKLUSDT
    # res = main.buy_sell.open_long_market("PLAYUSDT", "1", product_type="USDT-FUTURES", margin_mode="isolated", margin_coin="USDT")
    # print(json.dumps(res, indent=2))
    # time.sleep(5)
    # # Close that LONG (market)
    # res2 = main.buy_sell.close_long_market("PLAYUSDT", "1", product_type="USDT-FUTURES", margin_mode="isolated", margin_coin="USDT")
    # print(json.dumps(res2, indent=2))
