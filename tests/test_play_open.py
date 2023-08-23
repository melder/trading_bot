# pylint: skip-file
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


### Setup


@pytest.fixture(autouse=True)
def po_wrapped(expr=_EXPR, ticker=_TICKER, min_ticks=_MIN_TICKS):
    po = play_open.PlayOpen(expr)
    po.buy_data["call"] = min_ticks
    po.buy_data["put"] = min_ticks
    po.buy_data["ticker"] = ticker
    return po


# disables decorators
@pytest.fixture()
def po(po_wrapped, unwrap):
    po_wrapped.open_order = unwrap(po_wrapped.open_order)
    po_wrapped.open_orders = unwrap(po_wrapped.open_orders)
    po_wrapped.confirm_order = unwrap(po_wrapped.confirm_order)
    return po_wrapped


### Teardown - kinda situational so would rather shove in specialized fixture
### Maybe move tests that require teardown to separate file?


def delete_redis_objects(*oids):
    for oid in oids:
        if o := order.find(oid):
            o.o.delete(o.id)


@pytest.fixture()
def purge_all_orders(po):
    yield po
    delete_redis_objects(
        canned._BUY_CALL_OID,
        canned._BUY_PUT_OID,
        canned._SELL_CALL_OID,
        canned._SELL_PUT_OID,
    )


# Sanity test
def test_play_open_init(po):
    assert po.expr == _EXPR


### Verify canned data
### Otherwise utility is limited since can't monkeypatch multiple times
### per test ...


@pytest.fixture()
def patch_buy_call_to_open(monkeypatch):
    def mock_buy_call_response(*args, **kwargs):
        return canned.buy_call_unconfirmed()

    monkeypatch.setattr(play_open.hood, "buy_to_open", mock_buy_call_response)


@pytest.fixture()
def patch_buy_put_to_open(monkeypatch):
    def mock_buy_put_response(*args, **kwargs):
        return canned.buy_put_unconfirmed()

    monkeypatch.setattr(play_open.hood, "buy_to_open", mock_buy_put_response)


@pytest.fixture()
def patch_sell_call_to_open(monkeypatch):
    def mock_sell_call_response(*args, **kwargs):
        return canned.sell_call_unconfirmed()

    monkeypatch.setattr(play_open.hood, "buy_to_open", mock_sell_call_response)


@pytest.fixture()
def patch_sell_put_to_open(monkeypatch):
    def mock_sell_put_response(*args, **kwargs):
        return canned.sell_put_unconfirmed()

    monkeypatch.setattr(play_open.hood, "buy_to_open", mock_sell_put_response)


def test_buy_call_creates_order_object(po, patch_buy_call_to_open, purge_all_orders):
    res = po.open_order(po, "call")
    assert res == play_open.hood.buy_to_open("SPY", _EXPR, {})["id"]

    o = order.find(res)
    assert o.state == "unconfirmed"
    assert o.direction == "debit"
    assert o.option_type == "call"


def test_buy_put_creates_order_object(po, patch_buy_put_to_open, purge_all_orders):
    res = po.open_order(po, "put")
    assert res == play_open.hood.buy_to_open("SPY", _EXPR, {})["id"]

    o = order.find(res)
    assert o.state == "unconfirmed"
    assert o.direction == "debit"
    assert o.option_type == "put"


def test_sell_call_creates_order_object(po, patch_sell_call_to_open, purge_all_orders):
    res = po.open_order(po, "call")
    assert res == play_open.hood.buy_to_open("SPY", _EXPR, {})["id"]

    o = order.find(res)
    assert o.state == "unconfirmed"
    assert o.direction == "credit"
    assert o.option_type == "call"


def test_sell_put_creates_order_object(po, patch_sell_put_to_open, purge_all_orders):
    res = po.open_order(po, "put")
    assert res == play_open.hood.buy_to_open("SPY", _EXPR, {})["id"]

    o = order.find(res)
    assert o.state == "unconfirmed"
    assert o.direction == "credit"
    assert o.option_type == "put"


### Functional tests (perhaps move to separate file?)

# sadly this makes the orders synchronous, but for testing purposes that should
# be ok. if i'm really bored i'll look into the "pickle" error
def open_orders(po):
    return [po.open_order("call"), po.open_order("put")]


@pytest.fixture()
def patch_open_order(po, monkeypatch):
    def mock_open_order(o_type):
        js = (
            canned.buy_call_unconfirmed()
            if o_type == "call"
            else canned.buy_put_unconfirmed()
        )
        return order.create(js | po.buy_data[o_type]["min_ticks"])

    monkeypatch.setattr(po, "open_order", mock_open_order)


# 1. NEW ORDER: Open orders -> open call + put orders -> returns unconfirmed canned
def test_open_order(po, patch_open_order):
    o_call, o_put = open_orders(po)
    assert o_call.id == canned._BUY_CALL_OID
    assert o_call.state == "unconfirmed"
    assert o_call.direction == "debit"
    assert o_call.option_type == "call"
    assert o_put.id == canned._BUY_PUT_OID
    assert o_put.state == "unconfirmed"
    assert o_put.direction == "debit"
    assert o_put.option_type == "put"


# 2. Orders state changes from unconfirmed -> filled
@pytest.fixture()
def setup_filled(po):
    po.buy_call_oid = canned._BUY_CALL_OID
    po.buy_put_oid = canned._BUY_PUT_OID
    po.buy_orders["call"] = order.find(canned._BUY_CALL_OID)
    po.buy_orders["put"] = order.find(canned._BUY_PUT_OID)


@pytest.fixture()
def patch_get_hood_order_call_filled(po, setup_filled, monkeypatch):
    def mock_response(*args, **kwargs):
        return canned.buy_call_filled()

    monkeypatch.setattr(play_open.hood, "get_order_by_id", mock_response)


@pytest.fixture()
def patch_get_hood_order_put_filled(po, setup_filled, monkeypatch):
    def mock_response(*args, **kwargs):
        return canned.buy_put_filled()

    monkeypatch.setattr(play_open.hood, "get_order_by_id", mock_response)


def test_call_order_confirmed(po, setup_filled, patch_get_hood_order_call_filled):
    assert po.confirm_order(po, "call")


def test_put_order_confirmed(po, setup_filled, patch_get_hood_order_put_filled):
    assert po.confirm_order(po, "put")
