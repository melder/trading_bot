# pylint: disable=attribute-defined-outside-init
# pylint: disable=access-member-before-definition

from config import config  # pylint: disable=wrong-import-order

import operator

from pprint import pprint  # pylint: disable=unused-import
from typing import Optional

from redis_om import HashModel
from redis_om.model.model import NotFoundError

import date_helpers as dh
import discord_logging as dlog
import notifications

from helpers import key_join
from models import order

redis = config.redis

# namespaces

NS_INDEX = "i"
NS_CONDOR = "condor"
NS_STATE = "state"

# condor indexes

INDEX_STATE = key_join(NS_INDEX, NS_CONDOR, NS_STATE)

# condor states

_STATE_BUY_FILLED = "buy_filled"
_STATE_SELL_CONFIRMED = "sell_confirmed"
_STATE_CLOSED = "closed"
_STATE_FAILED = "failed"

# condor state indexes

_BUY_FILLED_CONDOR_INDEX = key_join(INDEX_STATE, _STATE_BUY_FILLED)
_SELL_CONFIRMED_CONDOR_INDEX = key_join(INDEX_STATE, _STATE_SELL_CONFIRMED)
_CLOSED_CONDOR_INDEX = key_join(INDEX_STATE, _STATE_CLOSED)
_FAILED_CONDOR_INDEX = key_join(INDEX_STATE, _STATE_FAILED)

_ALL_STATE_INDEXES = [
    _BUY_FILLED_CONDOR_INDEX,
    _SELL_CONFIRMED_CONDOR_INDEX,
    _CLOSED_CONDOR_INDEX,
    _FAILED_CONDOR_INDEX,
]


class CondorWrapper:
    """
    Wrapper to work around redis_om limitations
    """

    mutable_attrs = [
        "result",
        "locked",
        "sell_oid",
    ]

    @classmethod
    def new(cls, **kwargs):
        return cls(cls.Condor(**kwargs))

    @classmethod
    def get(cls, pk):
        return cls(cls.Condor.get(pk))

    def __init__(self, _condor):
        self.c = _condor
        for k, v in vars(self.c).items():
            setattr(self, k, v)

        self.ticker, self.expr = self.c.pk.split(":")
        self.state = self.current_state()

        self.o = order.find(self.oid)
        self.sell_o = None
        if self.sell_oid:
            self.sell_o = order.find(self.sell_oid)

        self.created_at = self.o.created_at

    def save(self):
        for k, v in vars(self).items():
            if k in self.mutable_attrs:
                setattr(self, k, v)
                setattr(self.c, k, v)
                if k == "sell_oid":
                    self.sell_o = order.find(self.sell_oid)
        self.c.save()
        return self

    def sync(self):
        self.o.sync()
        if self.sell_oid:
            self.sell_o.sync()
        return self

    ### condor state logic ###

    def change_to_state(self, to_state, from_state=None):
        if from_state:
            redis.srem(key_join(INDEX_STATE, from_state), self.pk)

        redis.sadd(key_join(INDEX_STATE, to_state), self.pk)
        setattr(self, "state", to_state)

    def current_state(self):
        for i in _ALL_STATE_INDEXES:
            if self.pk in redis.smembers(i):
                return i.rsplit(":", 1)[1]
        return "unknown"

    def buy_filled(self):
        self.change_to_state(_STATE_BUY_FILLED)
        notifications.condor_buy_filled(self)

    def sell_confirmed(self):
        if self.state == _STATE_BUY_FILLED:
            self.change_to_state(_STATE_SELL_CONFIRMED, from_state=_STATE_BUY_FILLED)
        else:
            dlog.error(
                f"{self.pk} - bad state transition: {self.state} -> sell_confirmed"
            )

    def sell_filled(self):
        if self.sell_o:
            return self.sell_o.sync().is_filled()
        return False

    def close(self, total_loss=False):
        if self.state == _STATE_BUY_FILLED:
            self.change_to_state(_STATE_CLOSED, from_state=_STATE_BUY_FILLED)
        if self.state == _STATE_SELL_CONFIRMED:
            self.change_to_state(_STATE_CLOSED, from_state=_STATE_SELL_CONFIRMED)
        notifications.condor_sell_filled(self, total_loss)

    def is_closed(self):
        return self.state == _STATE_CLOSED

    ### uncategorized ###

    def delete(self):
        for i in _ALL_STATE_INDEXES:
            redis.srem(i, self.pk)
        self.c.delete(self.pk)

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
        pprint(self.c.__dict__)
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

    class Condor(HashModel):
        """
        Condor model
        """

        oid: str
        sell_oid: Optional[str]
        result: Optional[str]
        locked: Optional[int]

        credit: float
        collateral: float
        multiplier_buy: float
        multiplier_sell: float
        target_roi: float

        enter_price: float


def find(ticker, expr):
    try:
        return CondorWrapper.get(f"{ticker}:{expr}")
    except NotFoundError:
        return None


def find_by_pk(pk):
    return CondorWrapper.get(pk)


def exists(ticker, expr):
    try:
        return find(ticker, expr)
    except NotFoundError:
        return False


def new(o, buy_data):
    if not o:
        return None

    return CondorWrapper.new(
        pk=key_join(o.ticker, o.expr),
        oid=o.id,
        enter_price=buy_data["enter_price"],
        credit=buy_data["credit"],
        collateral=buy_data["collateral"],
        multiplier_buy=buy_data["multiplier_buy"],
        multiplier_sell=buy_data["multiplier_sell"],
        target_roi=buy_data["target_roi"],
        locked=0,
    )


def seconds_until_expr(o):
    return dh.market_seconds_until_expr(o.expr, o.created_at)


def buy_filled_condors():
    res = [find_by_pk(s) for s in redis.smembers(_BUY_FILLED_CONDOR_INDEX)]
    res.sort(key=operator.attrgetter("created_at"))
    return res


def sell_confirmed_condors():
    res = [find_by_pk(s) for s in redis.smembers(_SELL_CONFIRMED_CONDOR_INDEX)]
    res.sort(key=operator.attrgetter("created_at"))
    return res


def closed_condors():
    res = [find_by_pk(s) for s in redis.smembers(_CLOSED_CONDOR_INDEX)]
    res.sort(key=operator.attrgetter("created_at"))
    return res


def closed_condors_for_week_ending(iso_date):
    return [s for s in closed_condors() if s.expr == iso_date]


def publish_eow_results(iso_date=None):
    _iso_date = iso_date or dh.today_date_utc()
    if dh.is_today_an_expr_date():
        d = notifications.aggregated_stats(closed_condors_for_week_ending(_iso_date))
        notifications.eow_results_pretty(d | {"expr": _iso_date})


def remove_condor_from_state_indexes(_condor):
    try:
        for i in _ALL_STATE_INDEXES:
            redis.srem(i, _condor.pk)
    except NotFoundError:
        pass


if __name__ == "__main__":
    pass
