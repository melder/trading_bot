from config import config  # pylint: disable=wrong-import-order

import multiprocessing
import operator
import sys
from datetime import datetime as dt
from pprint import pformat, pprint  # pylint: disable=unused-import

import date_helpers as dh
import discord_logging as dlog
import helpers
import hood
from aggregator import aggregator
from decorators import log, retry
from models import order, strangle

redis = config.redis

buy_params = config.conf.strangle
_MAX_BID = buy_params.max_bid
_ROI_MULTIPLIER = buy_params.roi_multiplier
_OPTIMAL_STRIKE_MULTIPLIER = buy_params.optimal_strike_multiplier
_STRANGLE_EJECT_TIME_RATIO = buy_params.strangle_eject_time_ratio

_MAX_BID_ASK_RATIO = 0.112  # (ask - bid) / (ask + padding)
_ASK_PADDING = 0.10
_MAX_COST_RATIO = 0.112  # 1 - min(total1, total2) / max(total1, total2)

_SLACK_MULTIPLIER = 1


class Buy:
    """
    Order buying logic + error handling
    """

    @classmethod
    def exec(cls, expr):
        return cls(expr).run()

    def __init__(self, expr):
        self.expr = expr

        self.buy_data = {}

        self.buy_call_oid = None
        self.buy_put_oid = None

        self.buy_orders = {}
        self.sell_orders = {}

    def run(self):
        # 1. Select optimal play
        self.buy_data = Select(self.expr).choose_play()
        if not self.buy_data:
            dlog.fatal("Could not find any plays!")
            sys.exit()

        # 2. Open orders
        self.open_orders()
        if not (self.buy_call_oid or self.buy_put_oid):
            self.handle_open_orders_errors()

        # 3. Confirm orders + cache for next market day sell
        if self.confirm_order("call") and self.confirm_order("put"):
            Cache.exec(
                self.expr, self.buy_data["ticker"], self.buy_call_oid, self.buy_put_oid
            )
            call_order = order.find(self.buy_call_oid)
            put_order = order.find(self.buy_put_oid)
            strangle.notifications.buy_orders_filled(
                call_order, put_order, _ROI_MULTIPLIER, _STRANGLE_EJECT_TIME_RATIO
            )
        else:
            self.handle_confirm_error()

    @log
    @retry(skip_first_delay=False)
    def confirm_order(self, o_type):
        self.buy_orders[o_type] = (
            order.find(self.buy_call_oid)
            if o_type == "call"
            else order.find(self.buy_put_oid)
        )
        return self.buy_orders[o_type].sync().is_filled()

    # using multiprocessing to execute orders in parallel ->
    # reducing chance that ask prices slide
    @log
    def open_orders(self):
        with multiprocessing.Pool() as p:
            self.buy_call_oid, self.buy_put_oid = p.map(
                self.open_order, ["call", "put"]
            )

    @log
    def open_order(self, o_type):
        if js := hood.buy_to_open(
            self.buy_data["ticker"], self.expr, o_type, self.buy_data[o_type]
        ):
            return order.create(js | self.buy_data[o_type]["min_ticks"]).id
        return None

    @log
    def cancel_orders(self):
        with multiprocessing.Pool() as p:
            p.map(self.cancel_order, [self.buy_call_oid, self.buy_put_oid])

    @log
    def cancel_order(self, oid):
        hood.cancel_order(oid)

    def handle_open_orders_errors(self):
        # considered NOOP if both legs fail to create order
        # log error, skip day and exit
        if not self.buy_call_oid and not self.buy_put_oid:
            dlog.fatal(self.error_string("Failed to create orders for both legs!"))
            sys.exit()

        # if only one leg filled:
        # 1. eject from filled immediately if possible
        # 2. if not, cache and sell next day
        # 3. still create strangle object - mark as failed
        # 4. No retries, just exit
        dlog.fatal("")
        sys.exit()

    def handle_confirm_error(self):
        call_order = order.find(self.buy_call_oid)
        put_order = order.find(self.buy_put_oid)

        # if orders not filled / partially filled cancel immediately
        # do not open strangle as no money was exchanged
        if call_order.no_contracts_filled() and put_order.no_contracts_filled():
            self.cancel_orders()
            dlog.fatal(self.error_string("No contracts filled - cancelling both legs"))
            sys.exit()
        # TODO: what if cancelling fails ?

        # if order filled / partially filled sell immediately (or next day)
        # open failed strangle since money was exchanged

    def error_string(self, title):
        return f"""{title}\n\n
-- Buy order data --\n\n
{pformat(self.buy_data)}"""


class Select:
    """
    1. Selects best valued option + handles validation
    2. Returns relevant data for PlayOpen to execute order
    """

    @classmethod
    def exec(cls, expr):
        return cls(expr).choose_play()

    def __init__(self, expr):
        self.expr = expr

    def choose_play(self, max_plays=50):
        for ticker in self.get_tickers()[:max_plays]:
            if strangle.exists(ticker, self.expr):
                continue
            if not (d := self.get_optimal_strikes(ticker)):
                continue
            if not self.validate(d):
                continue

            d["ticker"] = ticker
            d["call"] = d["call"] | {"quantity": round(_MAX_BID / d["call"]["ask"])}
            d["put"] = d["put"] | {"quantity": round(_MAX_BID / d["put"]["ask"])}
            return d

    # aggregator returns tickers sorted by option value
    def get_tickers(self):
        return aggregator()

    @retry
    def get_option_chain(self, ticker):
        return hood.get_option_chain(ticker, self.expr)

    def get_optimal_strikes(
        self, ticker, multiplier=_OPTIMAL_STRIKE_MULTIPLIER, slack=_SLACK_MULTIPLIER
    ):
        if not (chain := hood.get_option_chain(ticker, self.expr)):
            return None

        d = {"call": {}, "put": {}}
        roi = (1 + multiplier / 100) * 2

        optimal_call_val = sys.maxsize
        optimal_put_val = 0

        strike = None
        for c in chain:
            try:
                o_type = c.get("type").lower()
                strike = float(c.get("strike_price"))
                ask = float(c.get("ask_price"))
                bid = float(c.get("bid_price"))
                mark = float(c.get("mark_price"))
                min_ticks = c.get("min_ticks")
            except TypeError:
                dlog.warn(f"get_optimal_strikes: bad option - {ticker} ${strike}")
                continue

            # to maximize odds of a complete fill price is set to ask
            # also "slack" is extra few cents added to price
            # again with the purpose of avoiding partial fills
            price = ask
            if price > float(min_ticks["cutoff_price"]):
                price += slack * float(min_ticks["above_tick"])
            else:
                price += slack * float(min_ticks["below_tick"])

            if o_type == "call":
                target = strike + roi * mark
                if target < optimal_call_val:
                    optimal_call_val = target
                    d[o_type] = {
                        "strike": strike,
                        "ask": round(price, 2),
                        "bid": bid,
                        "mark": mark,
                        "target": optimal_call_val,
                        "min_ticks": min_ticks,
                    }
            else:
                target = strike - roi * mark
                if target > optimal_put_val:
                    optimal_put_val = target
                    d[o_type] = {
                        "strike": strike,
                        "ask": round(price, 2),
                        "bid": bid,
                        "mark": mark,
                        "target": optimal_call_val,
                        "min_ticks": min_ticks,
                    }

        return d

    def validate(self, d):
        call_data = d["call"]
        put_data = d["put"]

        return (
            call_data
            and put_data
            and self.validate_max_bid_price(call_data["ask"])
            and self.validate_max_bid_price(put_data["ask"])
            and self.validate_max_bid_ask_ratio(call_data["bid"], call_data["ask"])
            and self.validate_max_bid_ask_ratio(put_data["bid"], put_data["ask"])
            and self.validate_max_cost_ratio(call_data["ask"], put_data["ask"])
        )

    @staticmethod
    def validate_max_bid_price(price, max_bid=_MAX_BID):
        return price <= max_bid

    @staticmethod
    def validate_max_bid_ask_ratio(
        bid, ask, padding=_ASK_PADDING, max_ratio=_MAX_BID_ASK_RATIO
    ):
        bid_ask_ratio = (ask - bid) / (ask + padding)
        return bid_ask_ratio <= max_ratio

    @staticmethod
    def validate_max_cost_ratio(
        call_ask, put_ask, max_bid=_MAX_BID, max_ratio=_MAX_COST_RATIO
    ):
        total_buy_call = round(max_bid / call_ask) * call_ask
        total_buy_put = round(max_bid / put_ask) * put_ask
        cost_max = max([total_buy_call, total_buy_put])
        cost_min = min([total_buy_call, total_buy_put])

        return 1 - cost_min / cost_max <= max_ratio


class Cache:
    """
    Cache successful buy orders to open sell orders on following market day
    """

    namespace = "pending_orders"

    @classmethod
    def exec(cls, expr, ticker, call_oid, put_oid):
        cls(expr, ticker, call_oid, put_oid).cache_order()

    def __init__(self, expr, ticker, call_oid, put_oid):
        self.expr = expr
        self.ticker = ticker
        self.call_oid = call_oid
        self.put_oid = put_oid

    def key(self):
        return helpers.key_join(self.namespace, self.expr, self.ticker)

    def cache_order(self):
        h = {"call_oid": self.call_oid, "put_oid": self.put_oid}
        redis.hset(self.key(), mapping=h)

    @classmethod
    def get_orders(cls):
        return [
            {x: redis.hgetall(x)} for x in config.redis.scan_iter(f"*{cls.namespace}*")
        ]

    @classmethod
    def delete_strangle_key(cls, s):
        k = helpers.key_join(cls.namespace, s.expr, s.ticker)
        redis.delete(k)


class Sell:
    """
    1. Set next market day sells
    2. Cancel sells EOD if neither sell order fills. Repeat (1)
    3. Keep sell cache intact. Remove only after strangle is closed
    """

    @classmethod
    def exec(cls):
        cls().run()

    def __init__(self):
        self.cached_orders = []
        self.prepare_orders()

    def prepare_orders(self):
        for dicts in Cache.get_orders():
            for k, v in dicts.items():
                _, expr, ticker = k.split(":")
                call_order = order.find(v["call_oid"])
                put_order = order.find(v["put_oid"])
                seconds_left = min(
                    dh.market_seconds_until_expr(expr, call_order.created_at),
                    dh.market_seconds_until_expr(expr, put_order.created_at),
                )
                self.cached_orders.append(
                    {
                        "redis_key": k,
                        "expr": expr,
                        "ticker": ticker,
                        "buy_call_order": call_order,
                        "buy_put_order": put_order,
                        "seconds_left": seconds_left,
                    }
                )
        self.cached_orders.sort(key=operator.itemgetter("seconds_left"))

    # TODO: error handling
    def run(self):
        for d in self.cached_orders:
            d["sell_call_order"] = self.sell_to_close(d["buy_call_order"])
            d["sell_put_order"] = self.sell_to_close(d["buy_put_order"])
            if self.confirm(d["sell_call_order"]) and self.confirm(d["sell_put_order"]):
                self.init_strangle(d)
            else:
                pass  # error handle

    @log
    @retry(skip_first_delay=False)
    def confirm(self, o):
        o.sync()
        try:
            return o.is_confirmed() or o.is_filled() or o.is_partially_filled()
        except AttributeError:
            return None

    def sell_to_close(self, o, slack=_SLACK_MULTIPLIER):
        price = max(
            [
                self.multiplier_sell_price(o),
                self.bid_sell_price(o),
                self.intrinsic_value(o),
            ]
        )

        ticks = {
            "cutoff_price": o.cutoff_price,
            "above_tick": o.above_tick,
            "below_tick": o.below_tick,
        }

        if price >= o.cutoff_price:
            price = round(price / o.above_tick) * o.above_tick
            price -= o.above_tick * slack
        else:
            price = round(price / o.below_tick) * o.below_tick
            price -= o.below_tick * slack

        price = round(price, 2)

        if js := hood.sell_to_close(o, price):
            return order.create(js | ticks)
        return None

    @staticmethod
    def multiplier_sell_price(o, multiplier=_ROI_MULTIPLIER):
        sell_price = 2 * (1 + multiplier / 100)
        sell_price *= o.processed_premium / o.processed_quantity / 100
        return sell_price

    def bid_sell_price(self, o):
        if res := hood.get_option_chain_by_strike(o.ticker, o.expr, o.strike_price):
            for chain_data in res:
                if chain_data["type"] == o.option_type:
                    return round(float(chain_data.get("bid_price")), 2)

        return o.below_tick

    def intrinsic_value(self, o):
        if current_price := float(hood.get_price(o.ticker)):
            if o.option_type == "call":
                return current_price - o.strike_price
            if o.option_type == "put":
                return o.strike_price - current_price

        return o.below_tick

    @log
    def init_strangle(self, i):
        if s := strangle.find(i["ticker"], i["expr"]):
            s.append_sell_order(i["sell_call_order"])
            s.append_sell_order(i["sell_put_order"])
        else:
            s = strangle.new(i["buy_call_order"], i["buy_put_order"]).save()
            s.append_sell_order(i["sell_call_order"])
            s.append_sell_order(i["sell_put_order"])
            s.activate()


class Close:
    """
    Class that handles closing strategy.
    Most challenging / error-prone logic
    Demands highest priority on error correction / automated tests

    Default closing strategy:
        1. When leg wins, cancel opposite leg and sell at bid
        2. If neither leg wins and time expires, cancel and sell
           both at bid

    Advanced closing stratgies: TBD
    """

    @classmethod
    def exec(cls, s):
        if not s.locked:
            s.lock()
            cls(s).run()
            s.unlock()

    def __init__(self, _strangle):
        self.strangle = _strangle

    def run(self):
        self.strangle.sync()
        if self.close_if_filled():
            if self.confirm_sells_filled():
                self.strangle.result = "filled"
                self.strangle.save()
                self.close_strangle()
        if dh.make_offset_aware(dt.utcnow()) > self.strangle.eject_at:
            self.close_time_expired()
            if self.confirm_sells_filled():
                self.strangle.result = "ejected"
                self.strangle.save()
                self.close_strangle()

    def close_if_filled(self):
        # _extremely_ unlikely for both to fill
        if self.strangle.sell_is_filled("call") and self.strangle.sell_is_filled("put"):
            return True
        if self.strangle.call_sell_filled():
            self.eject(self.strangle.most_recent_sell_order("put"))
            return True
        if self.strangle.put_sell_filled():
            self.eject(self.strangle.most_recent_sell_order("call"))
            return True
        return False

    @log
    @retry(19, 3, skip_first_delay=False)
    def confirm_sells_filled(self):
        self.strangle.sync()
        return self.strangle.sells_filled()

    def close_strangle(self):
        self.strangle.close()
        Cache.delete_strangle_key(self.strangle)

    @log
    def close_time_expired(self):
        self.eject(self.strangle.most_recent_sell_order("call"))
        self.eject(self.strangle.most_recent_sell_order("put"))

    def eject(self, o):
        if js := self.cancel_and_sell(o):
            eject_o = order.create(js)
            self.strangle.append_sell_order(eject_o)

    def cancel_and_sell(self, o, slack=_SLACK_MULTIPLIER):
        if float(o.price) == float(o.below_tick):
            return None
        if not hood.cancel_order(o.id):
            return None
        if not (ec := self.eject_chain(o)):
            return None

        below_tick = float(ec["min_ticks"]["below_tick"])
        sell_price = max([below_tick, float(ec["bid_price"]) - slack * below_tick])
        sell_price = round(sell_price, 2)
        if not (js := hood.sell_to_close(o, sell_price)):
            return None

        pretty = pformat(js)
        dlog.info(f"close:cancel_and_sell {o.human_id} API response:\n\n{pretty}")

        return js | ec["min_ticks"]

    @staticmethod
    def eject_chain(o):
        if option_chains := hood.get_option_chain_by_strike(
            o.ticker, o.expr, o.strike_price
        ):
            for oc in option_chains:
                if oc["type"].lower() == o.option_type.lower():
                    return oc
        return None


def buy(expr):
    Buy.exec(expr)


def open_sells():
    Sell.exec()


def close_strangle(s):
    Close.exec(s)


def log_active_strangles():
    strangle.notifications.active_strangle_status(strangle.active_strangles())


if __name__ == "__main__":
    if sys.platform != "darwin":
        multiprocessing.set_start_method("spawn")
