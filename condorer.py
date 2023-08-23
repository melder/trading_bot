from config import config  # pylint: disable=wrong-import-order

import multiprocessing
import sys
from datetime import date
from pprint import pformat, pprint  # pylint: disable=unused-import

import discord_logging as dlog
import helpers  # pylint: disable=unused-import
import hood
from aggregator import aggregator
from decorators import log, retry
from models import order, condor


redis = config.redis
condor_params = config.conf.condor

_MAX_CONDORS = condor_params.max_condors
_MAX_COLLATERAL = condor_params.max_collateral
_OPTIMAL_STRIKE_MULTIPLIER_BUY = condor_params.optimal_strike_multiplier_buy
_OPTIMAL_STRIKE_MULTIPLIER_SELL = condor_params.optimal_strike_multiplier_sell
_MIN_CREDIT_COLLATERAL_RATIO = condor_params.min_credit_collateral_ratio
_TARGET_ROI = condor_params.target_roi

_BUY_SLACK = condor_params.buy_slack
_SELL_SLACK = condor_params.sell_slack


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

        self.oid = None
        self.order = None

        self.buy_slack = _BUY_SLACK

    def run(self):
        while self.buy_slack <= 2:
            # 1. Select optimal play
            self.buy_data = Select.exec(self.expr, self.buy_slack)
            if not self.buy_data:
                dlog.fatal("Could not find any plays!")
                sys.exit()

            # 2. Open condor
            self.order = self.open_order()
            self.oid = self.order.id

            # 3. Confirm condor order + set condor state to buy filled
            if self.confirm_order():
                c = self.init_condor()
                c.buy_filled()
                break

            # 4. If order not filled cancel order, reduce slack, and retry
            self.cancel_order(self.oid)

            self.buy_slack += 1

    @log
    def open_order(self):
        if js := hood.open_condor(self.buy_data["ticker"], self.expr, self.buy_data):
            return order.create(js | self.buy_data["min_ticks"])
        return None

    @log
    @retry(skip_first_delay=False, attempts=100)
    def confirm_order(self):
        return self.order.sync().is_filled()

    @log
    def init_condor(self, target_roi=_TARGET_ROI):
        if c := condor.find(self.order.ticker, self.order.expr):
            return c

        self.buy_data["target_roi"] = target_roi
        self.buy_data["enter_price"] = hood.get_price(self.order.ticker)

        res = hood.get_order_by_id(self.oid)
        self.buy_data["credit"] = round(
            float(res.get("processed_premium"))
            / float(res.get("processed_quantity"))
            / 100,
            2,
        )

        return condor.new(self.order, self.buy_data).save()

    @log
    def cancel_order(self, oid):
        hood.cancel_order(oid)

    # TODO: handle errors
    def handle_open_orders_errors(self):
        pass

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
    2. Returns necessary data for Buy to execute orders
    """

    @classmethod
    def exec(cls, expr, slack, dry_run=False):
        return cls(expr, slack).choose_play(dry_run=dry_run)

    def __init__(self, expr, slack):
        self.expr = expr
        self.slack = slack

    def choose_play(
        self,
        max_plays=100,
        max_collateral=_MAX_COLLATERAL,
        max_quantity=_MAX_CONDORS,
        dry_run=False,
    ):
        for ticker in self.get_tickers()[:max_plays]:
            if condor.exists(ticker, self.expr):
                continue
            if not (d := self.get_optimal_strikes(ticker)):
                continue

            if not dry_run and not self.validate(d):
                continue

            if not (quantity := min(max_quantity, max_collateral // d["collateral"])):
                continue

            d["ticker"] = ticker
            d["quantity"] = quantity

            if dry_run:
                pprint(d)
                continue

            return d

    # aggregator returns tickers sorted by option value
    def get_tickers(self):
        return aggregator(self.expr)

    @retry
    def get_option_chain(self, ticker):
        return hood.get_option_chain(ticker, self.expr)

    def get_optimal_strikes(
        self,
        ticker,
        multiplier_buy=_OPTIMAL_STRIKE_MULTIPLIER_BUY,
        multiplier_sell=_OPTIMAL_STRIKE_MULTIPLIER_SELL,
    ):
        if not (chain := hood.get_option_chain(ticker, self.expr)):
            return None

        d = {"call": {}, "put": {}}
        roi_buy = (1 + multiplier_buy / 100) * 2
        roi_sell = (1 + multiplier_sell / 100) * 2

        optimal_call_val_buy = sys.maxsize
        optimal_put_val_buy = 0
        optimal_call_val_sell = sys.maxsize
        optimal_put_val_sell = 0

        strike = None
        strikes = set()
        chain_data = {"call": {}, "put": {}}
        for c in chain:
            try:
                o_type = c.get("type").lower()
                strike = float(c.get("strike_price"))
                ask = float(c.get("ask_price"))
                bid = float(c.get("bid_price"))
                mark = float(c.get("mark_price"))
                min_ticks = c.get("min_ticks")

                strikes.add(strike)
                chain_data[o_type][strike] = {"ask": ask, "bid": bid}
            except TypeError:
                dlog.warn(f"get_optimal_strikes: bad option - {ticker} ${strike}")
                continue

            d["min_ticks"] = min_ticks

            if o_type == "call":
                target = strike + roi_buy * mark
                if target < optimal_call_val_buy:
                    optimal_call_val_buy = target
                    d[o_type]["buy"] = {
                        "strike": strike,
                        "ask": ask,
                        "bid": bid,
                        "mark": mark,
                        "target": optimal_call_val_buy,
                    }

                target = strike + roi_sell * mark
                if target < optimal_call_val_sell:
                    optimal_call_val_sell = target
                    d[o_type]["sell"] = {
                        "strike": strike,
                        "ask": ask,
                        "bid": bid,
                        "mark": mark,
                        "target": optimal_call_val_sell,
                    }

            else:
                target = strike - roi_buy * mark
                if target > optimal_put_val_buy:
                    optimal_put_val_buy = target
                    d[o_type]["buy"] = {
                        "strike": strike,
                        "ask": ask,
                        "bid": bid,
                        "mark": mark,
                        "target": optimal_put_val_buy,
                        "min_ticks": min_ticks,
                    }

                target = strike - roi_sell * mark
                if target > optimal_put_val_sell:
                    optimal_put_val_sell = target
                    d[o_type]["sell"] = {
                        "strike": strike,
                        "ask": ask,
                        "bid": bid,
                        "mark": mark,
                        "target": optimal_put_val_sell,
                        "min_ticks": min_ticks,
                    }

        # strike extermity check (cleanup: move to validation)
        strikes = sorted(list(strikes))

        if "buy" not in d["call"] or "buy" not in d["put"]:
            return None
        if "sell" not in d["call"] or "sell" not in d["put"]:
            return None

        if d["call"]["buy"]["strike"] == d["call"]["sell"]["strike"]:
            i = strikes.index(d["call"]["buy"]["strike"])
            if i == len(strikes) - 1:
                dlog.warn("CALL leg: sell target is highest strike")
                return None
            d["call"]["buy"]["strike"] = strikes[i + 1]
            d["call"]["buy"]["ask"] = chain_data["call"][strikes[i + 1]]["ask"]
            d["call"]["buy"]["bid"] = chain_data["call"][strikes[i + 1]]["bid"]

        if d["put"]["buy"]["strike"] == d["put"]["sell"]["strike"]:
            i = strikes.index(d["put"]["buy"]["strike"])
            if i == 0:
                dlog.warn("PUT leg: sell target is lowest strike")
                return None
            d["put"]["buy"]["strike"] = strikes[i - 1]
            d["put"]["buy"]["ask"] = chain_data["put"][strikes[i - 1]]["ask"]
            d["put"]["buy"]["bid"] = chain_data["put"][strikes[i - 1]]["bid"]
        # end srike extermity check

        d["collateral"] = max(
            d["call"]["buy"]["strike"] - d["call"]["sell"]["strike"],
            d["put"]["sell"]["strike"] - d["put"]["buy"]["strike"],
        )

        d["call"]["credit"] = round(
            d["call"]["sell"]["bid"] - d["call"]["buy"]["ask"], 2
        )
        d["put"]["credit"] = round(d["put"]["sell"]["bid"] - d["put"]["buy"]["ask"], 2)

        d["credit"] = d["call"]["credit"] + d["put"]["credit"]
        d["credit_collateral_ratio"] = d["credit"] / d["collateral"] * 100

        d["credit_with_slack"] = d["credit"] - self.slack * d["collateral"] / 100
        d["credit_with_slack_collateral_ratio"] = (
            d["credit_with_slack"] / d["collateral"] * 100
        )

        d["multiplier_buy"] = multiplier_buy
        d["multiplier_sell"] = multiplier_sell

        # pprint(d)
        return d

    def validate(self, d):
        call_data = d["call"]
        put_data = d["put"]

        return (
            call_data
            and put_data
            and self.validate_collateral(d)
            and self.validate_min_collateral_ratio(d)
        )

    @staticmethod
    def validate_collateral(d, max_collateral=_MAX_COLLATERAL):
        return d["collateral"] <= max_collateral

    @staticmethod
    def validate_min_collateral_ratio(d, ratio=_MIN_CREDIT_COLLATERAL_RATIO):
        return d["credit_collateral_ratio"] >= ratio


class Sell:
    """
    Sell strategy:
    1. Buy back condor for credit / roi_target
    2. Open the following day to avoid PDT
    """

    @classmethod
    def exec(cls):
        cls().run()

    def __init__(self):
        self.condors = condor.buy_filled_condors()

    @log
    def run(self):
        for _condor in self.condors:
            _order = _condor.o
            if not _order:
                continue

            price = (
                _condor.credit * (100 + _condor.target_roi)
                - _condor.target_roi * _condor.collateral
            ) / 100

            if js := hood.close_condor(_condor, price=price):
                o = order.create(js | _order.min_ticks)
                _condor.sell_oid = o.id
                _condor.save()
                _condor.sell_confirmed()


class Close:
    """
    Close strategy:
    1. If condor sell is filled, set condor state to closed
    2. If condor is not filled:
        a. cancel condor sell
        b. force sell
    """

    @classmethod
    def exec(cls):
        cls().run()

    def __init__(self):
        self.condors = condor.sell_confirmed_condors()
        self.sell_slack = condor_params.sell_slack

    @log
    def run(self):
        for _condor in self.condors:
            _order = _condor.sell_o
            if not _order:
                continue

            _order.sync()

            if _order.is_filled():
                _condor.close()
                continue

            if _condor.expr != date.today().isoformat():
                continue

            # eject scenario
            price = hood.eject_price_condor(_condor) - self.sell_slack / 100
            while price < _condor.collateral:
                if not hood.cancel_order(_condor.sell_oid):
                    dlog.warn(f"{_condor.pk} - Failed to cancel order. Skipping ...")
                    break

                if js := hood.close_condor(_condor, price=price):
                    o = order.create(js | _order.min_ticks)
                    _condor.sell_oid = o.id
                    _condor.save()
                    if self.confirm_order(_condor):
                        _condor.close()
                        break

                price += 0.01

            if _condor.is_closed():
                continue

            _condor.close(total_loss=True)

    @log
    @retry(skip_first_delay=False, attempts=5)
    def confirm_order(self, _condor):
        return _condor.sell_o.sync().is_filled()


def buy(expr):
    Buy.exec(expr)


def sell():
    Sell.exec()


def close():
    Close.exec()


if __name__ == "__main__":
    if sys.platform != "darwin":
        multiprocessing.set_start_method("spawn")

    buy("2023-04-21")
