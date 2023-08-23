import robin_stocks.robinhood as r
import pyotp
from config import config


def hood():
    login = config.conf.hood.login
    passw = config.conf.hood.password
    my2fa = config.conf.hood.my2fa
    pickle = config.conf.hood.pickle_name

    totp = pyotp.TOTP(my2fa).now()
    r.login(login, passw, mfa_code=totp, pickle_name=pickle)


def polygon_api_key():
    return config.conf.polygon.api_key
