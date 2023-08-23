# pylint: skip-file
from decorators import delay
from datetime import datetime, date, timedelta
from models import order as o, strangle
from uuid import uuid1, uuid4
import date_helpers as dh


# Orders to look at
#
# A. call debit
# B. put debit
# C. call credit
# D. put credit
#
# (Draw state machine)
# 1. Init state- unconfirmed buy order
# 2. queued?
# 3. confirmed buy order
# 4. partially_filled order
# 5. filled
# 6. rejected
# 7. cancelled
# 8. failed
#

# TODO:
# add complex executions nested object:


class Order:

    default_chain_symbol = "AAPL"
    default_time_in_force = "gfd"

    root_keys = [
        "account_number",
        "cancel_url",
        "canceled_quantity",
        "created_at",
        "direction",
        "id",
        "legs",
        "pending_quantity",
        "premium",
        "processed_premium",
        "price",
        "processed_quantity",
        "quantity",
        "ref_id",
        "state",
        "time_in_force",
        "trigger",
        "type",
        "updated_at",
        "chain_id",
        "chain_symbol",
        "response_category",
        "opening_strategy",
        "closing_strategy",
        "stop_price",
        "form_source",
        "client_bid_at_submission",
        "client_ask_at_submission",
        "client_time_at_submission",
    ]

    mutable_root_keys = [
        "id",
        "ticker",
        "state",
        "strike_price",
        "type",
        "direction",
        "pending_quantity",
        "price",
        "processed_quantity",
        "premium",
        "processed_premium",
        "executions",
        "time_in_force",
    ]

    leg_keys = [
        "executions",
        "expiration_date",
        "leg_id",
        "long_strategy_code",
        "option",
        "option_type",
        "position_effect",
        "ratio_quantity",
        "short_strategy_code",
        "side",
        "strike_price",
    ]

    execution_keys = ["id", "price", "quantity", "settlement_date", "timestamp"]

    @property
    def account_number(self):
        return "123456789"

    @property
    def cancel_url(self):
        if self.state == "filled":
            return None
        return f"https://api.robinhood.com/options/orders/{self.id}/cancel/"

    @property
    def canceled_quantity(self):
        return "%.5f" % self._canceled_quantity

    @canceled_quantity.setter
    def canceled_quantity(self, value):
        self._canceled_quantity = value

    @property
    def chain_id(self):
        return str(uuid4())

    @property
    def chain_symbol(self):
        return self._chain_symbol

    @chain_symbol.setter
    def chain_symbol(self, value):
        self._chain_symbol = value

    @property
    def closing_strategy(self):
        if self.direction == "credit":
            if self.o_type == "call":
                return "long_call"
            if self.o_type == "put":
                return "long_put"
        return None

    @property
    def created_at(self):
        return datetime.utcnow().isoformat() + "Z"

    @property
    def id(self):
        return str(uuid1())

    @property
    def legs(self):
        return []

    @property
    def opening_strategy(self):
        if self.direction == "debit":
            if self.o_type == "call":
                return "long_call"
            if self.o_type == "put":
                return "long_put"
        return None

    @property
    def pending_quantity(self):
        return "%.5f" % self._pending_quantity

    @pending_quantity.setter
    def pending_quantity(self, value):
        if self.state == "unconfirmed" or self.state == "queued":
            self._canceled_quantity = 0
            self._pending_quantity = value
            self._processed_quantity = 0
            self._processed_premium = 0
            self._quantity = value
        if self.state == "filled":
            self._canceled_quantity = 0
            self._pending_quantity = 0
            self._processed_quantity = value
            self._quantity = value
            self._processed_premium = int(float(self.premium) * float(self.quantity))

    @property
    def premium(self):
        return "%.8f" % self._premium

    @property
    def price(self):
        return "%.8f" % self._price

    @price.setter
    def price(self, value):
        self._price = value
        self._premium = value * 100

    @property
    def processed_premium(self):
        return str(self._processed_premium)

    @processed_premium.setter
    def processed_premium(self, value):
        self._processed_premium = value

    @property
    def processed_quantity(self):
        return "%.5f" % self._processed_quantity

    @processed_quantity.setter
    def processed_quantity(self, value):
        self._processed_quantity = value

    @property
    def quantity(self):
        return "%.5f" % self._quantity

    @quantity.setter
    def quantity(self, value):
        self._quantity = value

    @property
    def ref_id(self):
        return str(uuid4())

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = value

    @property
    def time_in_force(self):
        return self._time_in_force

    @time_in_force.setter
    def time_in_force(self, value):
        self._time_in_force = value

    @property
    def trigger(self):
        return "immediate"

    @property
    def updated_at(self):
        return datetime.utcnow().isoformat() + "Z"

    # special case since type is reserved
    def _type(self):
        return {"type": "limit"}

    # LEG PROPERTIES
    @property
    def executions(self):
        return []

    @property
    def expiration_date(self):
        return dh.next_expr()

    @property
    def leg_id(self):
        return str(uuid1(clock_seq=2))

    @property
    def long_strategy_code(self):
        return self._long_strategy_code

    @long_strategy_code.setter
    def long_strategy_code(self, value):
        self._long_strategy_code = value

    @property
    def option(self):
        return f"https://api.robinhood.com/options/instruments/{self.long_strategy_code[:-3]}/"

    @property
    def option_type(self):
        return self.o_type

    @property
    def position_effect(self):
        return "open"

    @property
    def ratio_quantity(self):
        return 1

    @property
    def short_strategy_code(self):
        return self.long_strategy_code[:-2] + "S1"

    @short_strategy_code.setter
    def short_strategy_code(self, value):
        self._short_strategy_code = value

    @property
    def side(self):
        if self.direction == "debit":
            return "buy"
        if self.direction == "credit":
            return "sell"
        return None

    @property
    def strike_price(self):
        return "%.4f" % self._strike_price

    @strike_price.setter
    def strike_price(self, value):
        self._strike_price = value

    # EXECUTION PROPERTIES
    @property
    def execution_id(self):
        return str(uuid1(clock_seq=4))

    @property
    def execution_price(self):
        return self._price

    @property
    def execution_quantity(self):
        return self._processed_quantity

    @property
    def settlement_date(self):
        return date.today().isoformat()

    @property
    def timestamp(self):
        return datetime.utcnow().isoformat() + "Z"

    def __init__(self, **kwargs):
        if not "chain_symbol" in kwargs:
            kwargs["chain_symbol"] = self.default_chain_symbol
        if not "time_in_force" in kwargs:
            kwargs["time_in_force"] = self.default_time_in_force
        if not "long_strategy_code" in kwargs:
            kwargs["long_strategy_code"] = str(uuid4()) + "_L1"
        [self.__setattr__(key, kwargs.get(key)) for key in kwargs.keys()]

    def as_json(self):
        js = {}
        for key in self.root_keys:
            try:
                js[key] = self.__getattribute__(key)
            except AttributeError:
                js[key] = None

        js_leg = {}
        for key in self.leg_keys:
            k = "id" if key == "leg_id" else key
            try:
                js_leg[k] = self.__getattribute__(key)
            except AttributeError:
                js_leg[k] = None
        js["legs"].append(js_leg)

        if self.state == "filled":
            js_exec = {}
            for key in self.execution_keys:
                k = "id" if key == "leg_id" else key
                k = "price" if key == "execution_price" else key
                k = "quantity" if key == "execution_quantity" else key
                try:
                    js_exec[k] = self.__getattribute__(key)
                except AttributeError:
                    js_exec[k] = None
            js["legs"][0]["executions"].append(js_exec)

        return js | self._type()

    @classmethod
    def construct_order(cls, **kwargs):
        return cls(kwargs)


def unconfirmed_call_buy(**kwargs):
    return Order(
        **({"o_type": "call", "direction": "debit", "state": "unconfirmed"} | kwargs)
    )


def unconfirmed_put_buy(**kwargs):
    return Order(
        **({"o_type": "put", "direction": "debit", "state": "unconfirmed"} | kwargs)
    )


def queued_call_buy(**kwargs):
    return Order(
        **({"o_type": "call", "direction": "debit", "state": "queued_"} | kwargs)
    )


def queued_put_buy(**kwargs):
    return Order(
        **({"o_type": "put", "direction": "debit", "state": "queued_"} | kwargs)
    )


def filled_call_buy(**kwargs):
    return Order(
        **({"o_type": "call", "direction": "debit", "state": "filled"} | kwargs)
    )


def filled_put_buy(**kwargs):
    return Order(
        **({"o_type": "put", "direction": "debit", "state": "filled"} | kwargs)
    )
