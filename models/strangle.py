# pylint: disable=attribute-defined-outside-init
# pylint: disable=access-member-before-definition

from config import config  # pylint: disable=wrong-import-order

import operator

from pprint import pprint  # pylint: disable=unused-import
from typing import Optional

from redis_om import HashModel
from redis_om.model.model import NotFoundError

import date_helpers as dh
import notifications

from helpers import key_join
from models import order

redis = config.redis

# namespaces

NS_INDEX = "i"
NS_STRANGLE = "strangle"
NS_STATE = "state"
NS_SELL_ORDERS_CALLS = "sell_orders:calls"
NS_SELL_ORDERS_PUTS = "sell_orders:puts"

# strangle indexes

INDEX_STATE = key_join(NS_INDEX, NS_STRANGLE, NS_STATE)

# strangle states

_STATE_ACTIVE = "active"
_STATE_CLOSED = "closed"
_STATE_FAILED = "failed"

# strangle state indexes

_ACTIVE_STRANGLE_INDEX = key_join(INDEX_STATE, _STATE_ACTIVE)
_CLOSED_STRANGLE_INDEX = key_join(INDEX_STATE, _STATE_CLOSED)
_FAILED_STRANGLE_INDEX = key_join(INDEX_STATE, _STATE_FAILED)

_ALL_STATE_INDEXES = [
    _ACTIVE_STRANGLE_INDEX,
    _CLOSED_STRANGLE_INDEX,
    _FAILED_STRANGLE_INDEX,
]

# other

_MIN_MINUTES_BEFORE_CLOSE = 3


class StrangleWrapper:
    """
    Wrapper to work around redis_om limitations
    """

    mutable_attrs = [
        "result",
        "locked",
    ]

    @classmethod
    def new(cls, **kwargs):
        return cls(cls.Strangle(**kwargs))

    @classmethod
    def get(cls, pk):
        return cls(cls.Strangle.get(pk))

    def __init__(self, _strangle):
        self.s = _strangle
        for k, v in vars(self.s).items():
            setattr(self, k, v)

        self.ticker, self.expr = self.s.pk.split(":")
        self.eject_at = dh.datetime_until_expr_from_market_seconds(
            self.s.eject_sec_to_expr, self.expr
        )

        self.buy_call_o = order.find(self.buy_call_oid)
        self.buy_put_o = order.find(self.buy_put_oid)

        self.sell_calls_key = _strangle.sell_call_oids
        self.sell_puts_key = _strangle.sell_put_oids

        self.state = self.current_state()
        self.created_at = min([self.buy_call_o.created_at, self.buy_put_o.created_at])

    def save(self):
        for k, v in vars(self).items():
            if k in self.mutable_attrs:
                setattr(self, k, v)
                setattr(self.s, k, v)
        self.s.save()
        return self

    def sync(self):
        sell_orders = self.get_sell_orders("call") + self.get_sell_orders("put")
        for o in sell_orders:
            if o.state in order.RH_ORDER_FINAL_STATES:
                continue
            o.sync()

    def orders(self):
        arr = [self.buy_call_o, self.buy_put_o]
        arr += self.get_sell_orders("call")
        arr += self.get_sell_orders("put")
        return arr

    ### redis ZSET sell order storage ###

    def append_sell_order(self, o):
        if o.direction == "credit":
            redis.zadd(zset_key(o), {o.id: o.created_at.timestamp()})

    def get_sell_orders(self, o_type):
        if o_type == "call":
            return [order.find(o) for o in redis.zrange(self.sell_call_oids, 0, -1)]
        return [order.find(o) for o in redis.zrange(self.sell_put_oids, 0, -1)]

    def most_recent_sell_order(self, o_type):
        return self.get_sell_orders(o_type)[-1]

    ### strangle layer BUY logic ###

    def buy_is_filled(self, o_type):
        if o_type == "call":
            return self.buy_call_o.is_filled()
        return self.buy_sell_o.is_filled()

    def are_buys_filled(self):
        return self.buy_is_filled("call") and self.buy_is_filled("put")

    ### strangle layer SELL logic ###

    # synthetic filled state:
    # 1. buy processed quantity is set and immutable
    # 2. sell processed quantity can be spread over multiple orders
    # 3. therefore sell quantity must be summed over multiple orders

    def get_sell_processed_quantity(self, o_type):
        return sum(o.processed_quantity for o in self.get_sell_orders(o_type))

    def get_sell_processed_premium(self, o_type):
        return sum(o.processed_premium for o in self.get_sell_orders(o_type))

    def sell_is_filled(self, o_type):
        pq = self.get_sell_processed_quantity(o_type)
        if o_type == "call":
            return pq == self.buy_call_o.processed_quantity
        return pq == self.buy_put_o.processed_quantity

    def call_sell_filled(self):
        return self.sell_is_filled("call")

    def put_sell_filled(self):
        return self.sell_is_filled("put")

    def sells_filled(self):
        return self.call_sell_filled() and self.put_sell_filled()

    ### strangle state logic ###

    def change_to_state(self, to_state, from_state=None):
        if from_state:
            k = key_join(INDEX_STATE, from_state)
            redis.srem(k, self.pk)
        k = key_join(INDEX_STATE, to_state)
        redis.sadd(k, self.pk)
        setattr(self, "state", to_state)

    def current_state(self):
        for i in _ALL_STATE_INDEXES:
            if self.pk in redis.smembers(i):
                return i.rsplit(":", 1)[1]
        return "unknown"

    def activate(self):
        self.change_to_state(_STATE_ACTIVE)
        notifications.strangle_open(find_by_pk(self.pk))

    def close(self):
        self.change_to_state(_STATE_CLOSED, _STATE_ACTIVE)
        notifications.strangle_close(find_by_pk(self.pk))

    ### uncategorized ###

    def delete(self):
        for i in _ALL_STATE_INDEXES:
            redis.srem(i, self.pk)
        self.s.delete(self.pk)

    def lock(self):
        self.locked = 1
        self.save()

    def unlock(self):
        self.locked = 0
        self.save()

    def dte(self):
        created_at = self.buy_call_o.created_at
        return (dh.market_closes_at(self.expr) - created_at).days - 1

    def debug(self, verbose=False):
        pprint(self.s.__dict__)
        if verbose:
            print("\nOrders:\n")
            for o in self.orders():
                if o:
                    pprint(f"{o.human_id}")
                    print("\n")
                    o.debug()
                    print("")

    def pretty(self):
        return f"{self.pk} - Eject at: {dh.dt_to_pretty(self.eject_at)}"

    class Strangle(HashModel):
        """
        Strangle model
        """

        buy_call_oid: str
        buy_put_oid: str
        sell_call_oids: str
        sell_put_oids: str
        eject_sec_to_expr: int
        result: Optional[str]
        locked: Optional[int]


def find(ticker, expr):
    try:
        return StrangleWrapper.get(f"{ticker}:{expr}")
    except NotFoundError:
        return None


def find_by_pk(pk):
    return StrangleWrapper.get(pk)


def exists(ticker, expr):
    try:
        return find(ticker, expr)
    except NotFoundError:
        return False


def zset_key(o):
    ns = NS_SELL_ORDERS_CALLS if o.option_type == "call" else NS_SELL_ORDERS_PUTS
    return key_join(ns, o.ticker, o.expr)


def new(buy_call, buy_put):
    if not (buy_call and buy_put):
        return None

    seconds_remaining = seconds_until_expr(buy_call, buy_put)
    eject_in = eject_in_adjusted(seconds_remaining, buy_call.expr)

    return StrangleWrapper.new(
        pk=key_join(buy_call.ticker, buy_call.expr),
        buy_call_oid=buy_call.id,
        buy_put_oid=buy_put.id,
        sell_call_oids=zset_key(buy_call),
        sell_put_oids=zset_key(buy_put),
        eject_sec_to_expr=eject_in,
        locked=0,
    )


def active_strangles():
    res = [find_by_pk(s) for s in redis.smembers(_ACTIVE_STRANGLE_INDEX)]
    res.sort(key=operator.attrgetter("eject_at"))
    return res


def closed_strangles():
    return [find_by_pk(s) for s in redis.smembers(_CLOSED_STRANGLE_INDEX)]


def closed_strangles_for_week_ending(iso_date):
    return [s for s in closed_strangles() if s.expr == iso_date]


def publish_eow_results(iso_date=None):
    _iso_date = iso_date or dh.today_date_utc()
    if dh.is_today_an_expr_date():
        d = notifications.aggregated_stats(closed_strangles_for_week_ending(_iso_date))
        notifications.eow_results_pretty(d | {"expr": _iso_date})


def remove_strangle_from_state_indexes(_strangle):
    try:
        for i in _ALL_STATE_INDEXES:
            redis.srem(i, _strangle.pk)
    except NotFoundError:
        pass


def seconds_until_expr(buy_call, buy_put):
    created_at = min([buy_call.created_at, buy_put.created_at])
    seconds = dh.market_seconds_until_expr(buy_call.expr, created_at)
    return int(seconds * config.conf.buy_params.strangle_eject_time_ratio)


# adjust exit to not be at very last minute
def eject_in_adjusted(seconds, expr, minutes_before_close=_MIN_MINUTES_BEFORE_CLOSE):
    dt_eject = dh.datetime_until_expr_from_market_seconds(seconds, expr)
    closes_at = dh.market_closes_at(dt_eject.date().isoformat())
    minutes_to_close = (closes_at - dt_eject).total_seconds() // 60
    if minutes_to_close < minutes_before_close:
        seconds += (minutes_before_close - minutes_to_close) * 60
    return seconds + 1


if __name__ == "__main__":
    pass
