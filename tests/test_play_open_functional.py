# pylint: skip-file
from config import config  # pylint: disable=wrong-import-order

import pytest
import play_open
import order
import tests.canned as canned
from pprint import pprint


### Default params

_EXPR = play_open.dh.all_exprs()[1]
_TICKER = "SPY"
_MIN_TICKS = {
    "min_ticks": {"above_tick": "0.01", "below_tick": "0.01", "cutoff_price": "0.00"}
}

redis = config.redis
redis_lookup_key = play_open._BUY_ORDERS_QUEUE


def purge_test_orders_from_redis():
    for oid in [
        canned._BUY_CALL_OID,
        canned._BUY_PUT_OID,
        canned._SELL_CALL_OID,
        canned._SELL_PUT_OID,
    ]:
        if o := order.find(oid):
            o.o.delete(o.id)


def purge_redis_lookup_key():
    redis.delete(redis_lookup_key)


def purge_test_strangle_from_redis():
    if s := play_open.strangle.find(_TICKER, _EXPR):
        s.s.delete(s.pk)
        play_open.strangle.remove_strangle_from_state_indexes(s)


@pytest.fixture(scope="module")
def min_ticks(mt=_MIN_TICKS):
    return mt["min_ticks"]


@pytest.fixture(scope="module")
def po_wrapped(min_ticks, expr=_EXPR, ticker=_TICKER):
    po = play_open.PlayOpen(expr)
    po.buy_data["call"] = min_ticks
    po.buy_data["put"] = min_ticks
    po.buy_data["ticker"] = ticker
    return po


@pytest.fixture(autouse=True, scope="module")
def po(po_wrapped, unwrap):
    po_wrapped.open_order = unwrap(po_wrapped.open_order)
    po_wrapped.open_orders = unwrap(po_wrapped.open_orders)
    po_wrapped.confirm_order = unwrap(po_wrapped.confirm_order)
    po_wrapped.confirm_sell_order = unwrap(po_wrapped.confirm_sell_order)
    po_wrapped.init_strangle = unwrap(po_wrapped.init_strangle)
    po_wrapped.cache_buy_orders = unwrap(po_wrapped.cache_buy_orders)
    yield po_wrapped
    # purge_test_orders_from_redis()
    # purge_test_strangle_from_redis()


# 1. SIMULATE BUY2OPEN ORDER: Open orders -> open call + put orders -> returns unconfirmed canned
@pytest.mark.parametrize(
    "res", [(canned.buy_call_unconfirmed()), (canned.buy_put_unconfirmed())]
)
def test_open_orders(min_ticks, res):
    o = order.create(res | min_ticks)
    assert o.id == res["id"]
    assert o.state == "unconfirmed"
    assert o.direction == "debit"
    assert o.option_type == res["legs"][0]["option_type"]


# 2. Orders state changes from unconfirmed -> filled
@pytest.mark.parametrize(
    "oid,res",
    [
        (canned._BUY_CALL_OID, (canned.buy_call_filled())),
        (canned._BUY_PUT_OID, (canned.buy_put_filled())),
    ],
)
def test_fill_orders(min_ticks, oid, res, monkeypatch):
    o = order.find(oid)

    def mock_hood_get_order_by_id(*args, **kwargs):
        return res | min_ticks

    monkeypatch.setattr(play_open.hood, "get_order_by_id", mock_hood_get_order_by_id)

    o.sync()
    assert o.id == res["id"]
    assert o.state == "filled"
    assert o.direction == "debit"
    assert o.option_type == res["legs"][0]["option_type"]


@pytest.mark.parametrize(
    "o_type,oid,res",
    [
        ("call", canned._BUY_CALL_OID, (canned.buy_call_filled())),
        ("put", canned._BUY_PUT_OID, (canned.buy_put_filled())),
    ],
)
def test_confirm_order(po, min_ticks, o_type, oid, res, monkeypatch):
    def mock_hood_get_order_by_id(*args, **kwargs):
        return res | min_ticks

    monkeypatch.setattr(play_open.hood, "get_order_by_id", mock_hood_get_order_by_id)

    if o_type == "call":
        po.buy_call_oid = oid
        po.buy_orders["call"] = order.find(po.buy_call_oid)
    if o_type == "put":
        po.buy_put_oid = oid
        po.buy_orders["put"] = order.find(po.buy_put_oid)
    assert po.confirm_order(po, o_type)


# 3. buy orders saved in FIFO queue for next day sell
def test_cache_buy_orders(po, monkeypatch):
    monkeypatch.setattr(
        play_open.log,
        "send_notification",
        lambda msg: None,
    )

    po.buy_data["call"] = canned.buy_call_filled()
    po.buy_data["put"] = canned.buy_put_filled()
    po.buy_data["call"]["min_ticks"] = _MIN_TICKS["min_ticks"]
    po.buy_data["put"]["min_ticks"] = _MIN_TICKS["min_ticks"]

    po.cache_buy_orders(po)
    res = po.get_queue()
    assert res[0] == f"{po.expr}:{_TICKER}:test_oid_buy_call:call:0.01:0.01:0.00"
    assert res[1] == f"{po.expr}:{_TICKER}:test_oid_buy_put:put:0.01:0.01:0.00"


# 4. test_next_day_sell
def test_next_day_sells(po, monkeypatch):
    monkeypatch.setattr(
        po,
        "execute_next_day_sells",
        lambda: None,
    )
    po.next_day_sells()
    assert po.buy_orders["call"].id == order.find(canned._BUY_CALL_OID).id
    assert po.buy_orders["put"].id == order.find(canned._BUY_PUT_OID).id
    purge_redis_lookup_key()


# 3. SIMULATE SELL2CLOSE ORDER
@pytest.mark.parametrize(
    "res", [(canned.sell_call_unconfirmed()), (canned.sell_put_unconfirmed())]
)
def test_open_sell_orders(min_ticks, res):
    o = order.create(res | min_ticks)
    assert o.id == res["id"]
    assert o.state == "unconfirmed"
    assert o.direction == "credit"
    assert o.option_type == res["legs"][0]["option_type"]


# 4. Orders state changes from unconfirmed -> confirmed
@pytest.mark.parametrize(
    "oid,res",
    [
        (canned._SELL_CALL_OID, (canned.sell_call_confirmed())),
        (canned._SELL_PUT_OID, (canned.sell_put_confirmed())),
    ],
)
def test_fill_sell_orders(min_ticks, oid, res, monkeypatch):
    o = order.find(oid)

    def mock_hood_get_order_by_id(*args, **kwargs):
        return res | min_ticks

    monkeypatch.setattr(play_open.hood, "get_order_by_id", mock_hood_get_order_by_id)

    o.sync()
    assert o.id == res["id"]
    assert o.state == "confirmed"
    assert o.direction == "credit"
    assert o.option_type == res["legs"][0]["option_type"]


@pytest.mark.parametrize(
    "o_type,oid,res",
    [
        ("call", canned._SELL_CALL_OID, (canned.sell_call_confirmed())),
        ("put", canned._SELL_PUT_OID, (canned.sell_put_confirmed())),
    ],
)
def test_confirm_sell_order(po, min_ticks, o_type, oid, res, monkeypatch):
    def mock_hood_get_order_by_id(*args, **kwargs):
        return res | min_ticks

    monkeypatch.setattr(play_open.hood, "get_order_by_id", mock_hood_get_order_by_id)

    if o_type == "call":
        po.sell_orders["call"] = order.find(oid)
    if o_type == "put":
        po.sell_orders["put"] = order.find(oid)
    assert po.confirm_sell_order(po, o_type)


# 5. All orders created -> init strangle
def test_init_strangle(po, monkeypatch):
    monkeypatch.setattr(play_open.dh, "market_seconds_until_expr", lambda x, y: 100000)
    monkeypatch.setattr(play_open.strangle, "adjust_eject_time", lambda x, y: 49560)
    monkeypatch.setattr(
        play_open.strangle.notifications, "strangle_open", lambda x: None
    )

    po.buy_orders["call"] = order.find(canned._BUY_CALL_OID)
    po.buy_orders["put"] = order.find(canned._BUY_PUT_OID)
    po.sell_orders["call"] = order.find(canned._SELL_CALL_OID)
    po.sell_orders["put"] = order.find(canned._SELL_CALL_OID)
    po.init_strangle(po)

    assert play_open.strangle.find(_TICKER, _EXPR).pk in [
        x.pk for x in play_open.strangle.active_strangles()
    ]
