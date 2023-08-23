from config import config  # pylint: disable=wrong-import-order

import multiprocessing
import sys
from datetime import date
from pprint import pformat, pprint  # pylint: disable=unused-import

import discord_logging as dlog
import hood
from decorators import log, retry
from models import order, condor


redis = config.redis
condor_params = config.conf.condor

_MAX_CONDORS = condor_params.max_condors
_MAX_COLLATERAL = condor_params.max_collateral
_OPTIMAL_STRIKE_MULTIPLIER_BUY = condor_params.optimal_strike_multiplier_buy
_OPTIMAL_STRIKE_MULTIPLIER_SELL = condor_params.optimal_strike_multiplier_sell
_MIN_CREDIT_COLLATERAL_RATIO = condor_params.min_credit_collateral_ratio_spy
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
    @retry(skip_first_delay=False, attempts=10)
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
        if oid:
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
    Neutered select -> SPY
    """

    ticker = "SPY"

    @classmethod
    def exec(cls, expr, slack, dry_run=False):
        return cls(expr, slack).choose_play(dry_run=dry_run)

    def __init__(self, expr, slack):
        self.expr = expr
        self.slack = slack

    def choose_play(
        self,
        max_collateral=_MAX_COLLATERAL,
        max_quantity=_MAX_CONDORS,
        dry_run=False,
    ):
        if not (d := self.get_optimal_strikes()):
            return None

        if not dry_run and not self.validate(d):
            return None

        if not (quantity := min(max_quantity, max_collateral // d["collateral"])):
            return None

        d["ticker"] = self.ticker
        d["quantity"] = quantity

        if dry_run:
            pprint(d)
            return None

        return d

    @retry
    def get_option_chain(self, ticker):
        return hood.get_option_chain(ticker, self.expr)

    def get_optimal_strikes(
        self,
        multiplier_buy=_OPTIMAL_STRIKE_MULTIPLIER_BUY,
        multiplier_sell=_OPTIMAL_STRIKE_MULTIPLIER_SELL,
    ):
        if not (chain := hood.get_option_chain(self.ticker, self.expr)):
            return None

        d = {"call": {}, "put": {}}
        roi_sell = (1 + multiplier_sell / 100) * 2

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
                dlog.warn(f"get_optimal_strikes: bad option - {self.ticker} ${strike}")
                continue

            d["min_ticks"] = min_ticks

            if o_type == "call":
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

                    d[o_type]["buy"] = {
                        "strike": strike,
                        "ask": ask,
                        "bid": bid,
                        "mark": mark,
                        "target": optimal_call_val_sell + 1,
                    }

            else:
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

                    d[o_type]["buy"] = {
                        "strike": strike,
                        "ask": ask,
                        "bid": bid,
                        "mark": mark,
                        "target": optimal_put_val_sell - 1,
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

            if js := hood.close_condor(_condor, slack=_SELL_SLACK):
                o = order.create(js | _order.min_ticks)
                _condor.sell_oid = o.id
                _condor.save()
                _condor.sell_confirmed()


def buy(expr):
    Buy.exec(expr)


def sell():
    Sell.exec()


if __name__ == "__main__":
    if sys.platform != "darwin":
        multiprocessing.set_start_method("spawn")

    buy("2023-05-09")
