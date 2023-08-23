from pprint import pprint, pformat  # pylint: disable=unused-import
import json

import robin_stocks.robinhood as rh

import auth
from decorators import retry, log_api
import discord_logging as log

auth.hood()

_MIC = "XNYS"  # NYSE market code

_API_RETRY_TRIES = 5
_API_RETRY_DELAY = 9  # second


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


def get_option_chain_by_strike_and_type(ticker, expr, strike, option_type):
    res = get_option_chain_by_strike(ticker, expr, strike)
    for option in res:
        if option["type"] == option_type:
            return option
    return None


def get_market_hours(iso_date):
    return rh.get_market_hours(_MIC, iso_date)


def closest_strikes_to_price(ticker, expr):
    try:
        price = float(get_price(ticker))
        options = get_tradable_options(ticker, expr, option_type="call")
        if list(filter(None, options)):
            return list(
                map(
                    lambda x: x["strike_price"],
                    sorted(
                        options, key=lambda o: abs(price - float(o["strike_price"]))
                    ),
                )
            )[:2]
    except TypeError as err:
        print(f"Unexpected {err=}, {type(err)=}")
        print(f"Failed to get option chain data for {ticker}")

    return [None, None]


def condensed_option_chain(ticker, expr):
    try:
        strike1, strike2 = closest_strikes_to_price(ticker, expr)
        if not (strike1 and strike2):
            return []
        res = get_option_chain_by_strike(ticker, expr, strike1)
        res += get_option_chain_by_strike(ticker, expr, strike2)
        return res if len(res) == 4 else []
    except ValueError:
        return []


def get_earnings(ticker):
    return rh.stocks.get_earnings(ticker)


def get_ticks(ticker):
    return {"min_ticks": rh.get_chains(ticker)["min_ticks"]}


@log_api
@retry(_API_RETRY_TRIES, _API_RETRY_DELAY)
def buy_to_open(ticker, expr, o_type, d):
    res = rh.orders.order_buy_option_limit(
        positionEffect="open",
        creditOrDebit="debit",
        timeInForce="gfd",
        symbol=ticker,
        expirationDate=expr,
        optionType=o_type,
        price=d["ask"],
        quantity=d["quantity"],
        strike=d["strike"],
        jsonify=False,
    )

    if js := json.loads(res._content):  # pylint: disable=protected-access
        if res.status_code in range(200, 300):
            return js
        log.warn(
            f"sell_to_close API returned a non 2** status: {res.status_code}\n\n{js}"
        )

    return None


@log_api
@retry(_API_RETRY_TRIES, _API_RETRY_DELAY)
def sell_to_close(o, price, time_in_force="gfd"):
    o.sync()

    res = rh.orders.order_sell_option_limit(
        positionEffect="close",
        creditOrDebit="credit",
        timeInForce=time_in_force,
        symbol=o.ticker,
        expirationDate=o.expr,
        optionType=o.option_type,
        price=price,
        quantity=o.quantity,
        strike=o.strike_price,
        jsonify=False,
    )

    if js := json.loads(res._content):  # pylint: disable=protected-access
        if res.status_code in range(200, 300):
            return js
        log.warn(
            f"""sell_to_close API returned a non 2** status: {res.status_code}:

    JS:

    {pformat(js)}

    Order Info:

    {pformat(o.__dict__)}"""
        )

    return None


@retry(_API_RETRY_TRIES + 1, _API_RETRY_DELAY, skip_first_delay=False)
def cancel_order(oid):
    # empty result indicates success
    if res := rh.orders.cancel_option_order(oid):
        log.warn(f"cancel_order API failed for {oid}:\n\n{res}")
        return False
    return True


@log_api
@retry(_API_RETRY_TRIES, _API_RETRY_DELAY)
def open_condor(ticker, expr, d):
    call_data = d.get("call")
    put_data = d.get("put")

    if not (call_data and put_data):
        return None

    spread = [
        {
            "expirationDate": expr,
            "strike": call_data["buy"]["strike"],
            "optionType": "call",
            "effect": "open",
            "action": "buy",
        },
        {
            "expirationDate": expr,
            "strike": call_data["sell"]["strike"],
            "optionType": "call",
            "effect": "open",
            "action": "sell",
        },
        {
            "expirationDate": expr,
            "strike": put_data["buy"]["strike"],
            "optionType": "put",
            "effect": "open",
            "action": "buy",
        },
        {
            "expirationDate": expr,
            "strike": put_data["sell"]["strike"],
            "optionType": "put",
            "effect": "open",
            "action": "sell",
        },
    ]

    res = rh.orders.order_option_spread(
        spread=spread,
        direction="credit",
        price=round(d["credit_with_slack"], 2),
        quantity=d["quantity"],
        symbol=ticker,
        timeInForce="gtc",
        jsonify=False,
    )

    if js := json.loads(res._content):  # pylint: disable=protected-access
        if res.status_code in range(200, 300):
            return js
        log.warn(
            f"""open_condor API returned a non 2** status: {res.status_code}:

    JS:

    {pformat(js)}"""
        )

    return None


@log_api
@retry(_API_RETRY_TRIES, _API_RETRY_DELAY)
def close_condor(_condor, slack=0.00, price=0.00):
    expr = _condor.expr

    _order = _condor.o
    _legs = _order.legs

    spread = []
    for leg in _legs:
        spread.append(
            {
                "expirationDate": expr,
                "strike": leg["strike_price"],
                "optionType": leg["option_type"],
                "effect": "close" if leg["position_effect"] == "open" else "open",
                "action": "sell" if leg["side"] == "buy" else "buy",
            }
        )

    if not price:
        price = int((100 - _condor.target_roi) * _condor.credit) / 100
        price += slack * _condor.collateral / 100

    res = rh.orders.order_option_spread(
        spread=spread,
        direction="debit",
        price=round(price, 2),
        quantity=_order.quantity,
        symbol=_condor.ticker,
        timeInForce="gtc",
        jsonify=False,
    )

    if js := json.loads(res._content):  # pylint: disable=protected-access
        if res.status_code in range(200, 300):
            return js
        log.warn(
            f"""open_condor API returned a non 2** status: {res.status_code}:

    JS:

    {pformat(js)}"""
        )

    return None


def eject_price_condor(_condor):
    price = 0.0

    res = get_order_by_id(_condor.oid)
    legs = [[x["strike_price"], x["option_type"], x["side"]] for x in res["legs"]]

    ticker, expr = _condor.ticker, _condor.expr
    for strike_type_side in legs:
        _strike, _type, _side = strike_type_side
        o = get_option_chain_by_strike_and_type(ticker, expr, _strike, _type)
        if not o:
            return -1
        if _side == "sell":
            price += float(o["bid_price"])
        if _side == "buy":
            price -= float(o["ask_price"])

    return min(round(price, 2), _condor.collateral)


def open_interest(ticker, expr):
    d = {}
    for chain in get_option_chain(ticker, expr):
        if "open_interest" not in chain:  # pylint: disable=unsupported-membership-test
            continue
        if chain["strike_price"] not in d:
            d[chain["strike_price"]] = {chain["type"]: chain["open_interest"]}
            continue
        d[chain["strike_price"]] |= {chain["type"]: chain["open_interest"]}

    return d
