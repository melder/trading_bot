# pylint: skip-file


from pprint import pprint  # pylint: disable=unused-import
import json
import random

import robin_stocks.robinhood as rh

import auth
from decorators import retry, log_api
import discord_logging as log

auth.hood()

import tests.canned as canned
import tests.helpers as helpers

_MIC = "XNYS"  # NYSE market code

_API_RETRY_TRIES = 10
_API_RETRY_DELAY = 6 # second

class HoodException(Exception):
    """
    catchall exception for hood API failures
    """


def get_order_by_id(oid):
    return rh.orders.get_option_order_info(oid)


def get_all_orders():
    return rh.options.get_aggregate_positions()


def get_open_orders():
    return rh.get_open_option_positions()


def get_price(ticker):
    return rh.stocks.get_latest_price(ticker)[0]


def get_chains(ticker):
    return rh.options.get_chains(ticker)


def get_tradable_options(ticker, expr, option_type=None):
    return rh.find_tradable_options(ticker, expr, optionType=option_type)


def get_option_chain(ticker, expr):
    try:
        return rh.options.find_options_by_expiration(ticker, expr)
    except AttributeError as err:
        print(f"Unexpected {err=}, {type(err)=}")
        print(f"Failed to get option chain data for {ticker}")
        return []
    except TypeError as err:
        print(f"Unexpected {err=}, {type(err)=}")
        print(f"Failed to get option chain data for {ticker}")
        return []


def get_option_chain_by_strike(ticker, expr, strike):
    try:
        return rh.options.find_options_by_expiration_and_strike(ticker, expr, strike)
    except AttributeError as err:
        print(f"Unexpected {err=}, {type(err)=}")
        print(f"Failed to get option chain data for {ticker}")
        return []
    except TypeError as err:
        print(f"Unexpected {err=}, {type(err)=}")
        print(f"Failed to get option chain data for {ticker}")
        return []


def get_market_hours(iso_date):
    return rh.get_market_hours(_MIC, iso_date)


def closest_strikes_to_price(ticker, expr):
    try:
        price = float(get_price(ticker))
        options = get_tradable_options(ticker, expr, option_type="call")
        if list(filter(None, options)):
            return list(map(lambda x: x['strike_price'],
                        sorted(options, key=lambda o: abs(price - float(o['strike_price'])))))[:2]
    except TypeError as err:
        print(f"Unexpected {err=}, {type(err)=}")
        print(f"Failed to get option chain data for {ticker}")

    return [None, None]


def condensed_option_chain(ticker, expr):
    strike1, strike2 = closest_strikes_to_price(ticker, expr)
    if not (strike1 and strike2):
        return []
    res = get_option_chain_by_strike(ticker, expr, strike1)
    res += get_option_chain_by_strike(ticker, expr, strike2)
    return res if len(res) == 4 else []


def get_earnings(ticker):
    return rh.stocks.get_earnings(ticker)


@retry(_API_RETRY_TRIES, _API_RETRY_DELAY)
def buy_to_open(o_type):
    if helpers.failure_chance(1, _in=10):
        return canned.buy_call_unconfirmed() if o_type == "call" else canned.buy_put_unconfirmed()
    if helpers.failure_chance(5, _in=10):
        pprint({'detail': 'This order is invalid because you do not have enough shares to close your position.'})
        return None
    pprint("Unknown error")
    return None



@retry(_API_RETRY_TRIES, _API_RETRY_DELAY)
def sell_to_close(o, sell_price):
    o.sync()

    res = rh.orders.order_sell_option_limit(
        positionEffect=   'close',
        creditOrDebit=    'credit',
        timeInForce=      'gtc',
        symbol=           o.ticker,
        expirationDate=   o.expr,
        optionType=       o.option_type,
        price=            sell_price,
        quantity=         o.quantity,
        strike=           o.strike_price,
        jsonify=          False
    )

    if (js := json.loads(res._content)):  # pylint: disable=protected-access
        if res.status_code in range(200,300):
            return js
        log.warn(f"sell_to_close API returned a non 2** status: {res.status_code}\n\n{js}")

    return None


# @retry(_API_RETRY_TRIES, _API_RETRY_DELAY)
def cancel_order(oid):
    # empty result indicates success
    if (res := rh.orders.cancel_option_order(oid)):
        log.warn(f"cancel_order API failed for {oid}:\n\n{res}")
        return False
    return True
