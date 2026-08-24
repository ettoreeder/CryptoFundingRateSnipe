import numpy as np
import matplotlib.pyplot as plt
import scipy.signal as sc
import time
import json

class APIKeys:
    """
    Container class to hold API keys and URLs needed globally in all classes.
    """
    def __init__(self):
        with open('CryptoFundingRateSnipe/API_keys.json') as f:
            keys = json.load(f)
        self.AccessAPIKey = keys['AccessAPIKey']
        self.SecretKey = keys['SecretKey']
        self.Passphrase = keys['Passphrase']

# ip command: curl ifconfig.me