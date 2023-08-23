from config import config  # pylint: disable=wrong-import-order

import datetime as dt
import json
from pprint import pprint  # pylint: disable=unused-import

from typing import Optional  # pylint: disable=unused-import
from redis_om import HashModel
from redis_om.model.model import NotFoundError

from helpers import key_join  # pylint: disable=unused-import
import hood


redis = config.redis

# RH ORDER STATES

_RH_ORDER_UNCONFIRMED = "unconfirmed"
_RH_ORDER_QUEUED = "queued"
_RH_ORDER_CONFIRMED = "confirmed"
_RH_ORDER_PARTIALLY_FILLED = "partially_filled"
_RH_ORDER_FILLED = "filled"
_RH_ORDER_REJECTED = "rejected"
_RH_ORDER_CANCELLED = "cancelled"
_RH_ORDER_FAILED = "failed"


RH_ORDER_FINAL_STATES = [
    _RH_ORDER_FILLED,
    _RH_ORDER_REJECTED,
    _RH_ORDER_CANCELLED,
    _RH_ORDER_FAILED,
]


class OrderWrapper:
    """
    Wrapper to extend redis_om functionality since its behavior is somewhat magical.
    Best strategy is to keep the models as concise as possible.
    """

    class Order(HashModel):
        """
        Order model
        Fields map to relevant keys of hood API order object
        Tailored for single legged option orders
        """

        chain_symbol: str
        chain_id: str
        direction: str
        state: str
        price: float
        quantity: float
        pending_quantity: float
        processed_quantity: float
        premium: float
        processed_premium: float
        cutoff_price: float
        above_tick: float
        below_tick: float
        created_at: dt.datetime
        updated_at: dt.datetime
        legs: str

    order_fields = list(Order.__fields__.keys())

    mutable_attrs_order = [
        "updated_at",
        "price",
        "premium",
        "quantity",
        "state",
        "pending_quantity",
        "processed_quantity",
        "processed_premium",
    ]

    @classmethod
    def parse(cls, js):
        d = js.copy()
        d["pk"] = d.pop("id")
        d["legs"] = json.dumps(d.pop("legs"))
        return {k: d[k] for k in cls.order_fields if k in d}

    @classmethod
    def new(cls, js):
        return cls(cls.Order(**cls.parse(js)))

    @classmethod
    def find(cls, oid):
        return cls(cls.Order.get(oid))

    def __init__(self, _order):
        self.o = _order

        for k, v in vars(self.o).items():
            if k in self.order_fields:
                setattr(self, k, v)

        # aliases
        self.id = self.pk
        self.ticker = self.chain_symbol

        # ticks
        self.min_ticks = {
            "above_tick": _order.above_tick,
            "below_tick": _order.below_tick,
            "cutoff_price": _order.cutoff_price,
        }

        # unpack + override legs
        self.legs = json.loads(self.legs)

        # assumption is that all leg expirations are the same
        self.expr = self.legs[0]["expiration_date"]

        # strangle specific params
        if len(self.legs) == 1:
            self.option_type = self.legs[0]["option_type"]
            self.strike_price = self.legs[0]["strike_price"]

            # pretty formatting for logging and such
            strike = round(float(self.strike_price), 2)
            if round(strike % 1, 2) == 0.00:
                strike = int(strike)
            self.human_id = " ".join(
                [
                    self.expr,
                    self.chain_symbol,
                    "$" + str(strike),
                    self.option_type.upper(),
                ]
            )

        self.actual_price = 0
        if float(self.o.processed_quantity) > 0:
            self.actual_price = (
                float(self.o.processed_premium) / float(self.o.processed_quantity) / 100
            )

    def save(self, d=None):
        for k, v in (d or vars(self)).items():
            if k in self.mutable_attrs_order:
                setattr(self, k, v)
                setattr(self.o, k, v)
        self.o.save()

        if float(self.o.processed_quantity) > 0:
            self.actual_price = (
                float(self.o.processed_premium) / float(self.o.processed_quantity) / 100
            )
        return self

    def sync(self):
        return self.save(self.parse(hood.get_order_by_id(self.id)))

    def is_put(self):
        return self.option_type == "put"

    def is_call(self):
        return self.option_type == "call"

    def is_queued(self):
        return self.state == _RH_ORDER_QUEUED

    def is_confirmed(self):
        return self.state == _RH_ORDER_CONFIRMED

    def is_filled(self):
        return self.state == _RH_ORDER_FILLED

    def is_partially_filled(self):
        return self.state == _RH_ORDER_PARTIALLY_FILLED

    def is_cancelled(self):
        return self.state == _RH_ORDER_CANCELLED

    def no_contracts_filled(self):
        return not (self.is_filled() and self.is_partially_filled())

    # state doesn't update immediately. wat do?
    def cancel(self):
        hood.cancel_order(self.id)
        return self.sync()

    def debug(self):
        pprint(self.o.__dict__)


def find(oid):
    try:
        return OrderWrapper.find(oid)
    except NotFoundError:
        return None


def create(js):
    return OrderWrapper.new(js).save()


if __name__ == "__main__":
    pass
