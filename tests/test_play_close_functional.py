# pylint: skip-file
import pytest
import play_close
import tests.canned as canned
from pprint import pprint

# hack to disable decorators
# https://stackoverflow.com/questions/1015307/python-bind-an-unbound-method
def bind(instance, func, as_name=None):
    """
    Bind the function *func* to *instance*, with either provided name *as_name*
    or the existing name of *func*. The provided *func* should accept the
    instance as the first argument, i.e. "self".
    """
    if as_name is None:
        as_name = func.__name__
    bound_method = func.__get__(instance, instance.__class__)
    setattr(instance, as_name, bound_method)
    return bound_method


class TestPlayCloseFunctional:
    @pytest.fixture()
    def pc_wrapped(self, explicit_test_mode):
        s = canned.create_mock_strangle()
        pc = play_close.PlayClose(s)
        pc.strangle.activate()
        yield pc
        pc.strangle.delete()

    # disables decorators
    @pytest.fixture()
    def pc(self, pc_wrapped, unwrap):
        pc_wrapped.close_if_filled = unwrap(pc_wrapped.close_if_filled)
        pc_wrapped.eject = unwrap(pc_wrapped.eject)
        pc_wrapped.close_time_expired = unwrap(pc_wrapped.close_time_expired)
        pc_wrapped.confirm_sells_filled = unwrap(pc_wrapped.confirm_sells_filled)

        bind(pc_wrapped, pc_wrapped.close_if_filled)
        bind(pc_wrapped, pc_wrapped.eject)
        bind(pc_wrapped, pc_wrapped.close_time_expired)
        bind(pc_wrapped, pc_wrapped.confirm_sells_filled)

        return pc_wrapped

    def test_strangle_sanity(self, pc):
        s = pc.strangle
        assert s
        assert s.ticker == "SPY"
        assert s.expr == canned._DEFAULT_EXPR
        assert s.buy_call_oid == canned._BUY_CALL_OID
        assert s.buy_put_oid == canned._BUY_PUT_OID
        assert s.sell_call_oid == canned._SELL_CALL_OID
        assert s.sell_put_oid == canned._SELL_PUT_OID

    # 1. Leg winning scenario
    @pytest.mark.parametrize(
        "sell_call_o,sell_put_o,expected",
        [
            (canned.sell_call_filled(), canned.sell_put_filled(), True),
            (canned.sell_call_confirmed(), canned.sell_put_filled(), True),
            (canned.sell_call_filled(), canned.sell_put_confirmed(), True),
            (canned.sell_call_confirmed(), canned.sell_put_confirmed(), False),
        ],
    )
    def test_close_if_filled(self, pc, monkeypatch, sell_call_o, sell_put_o, expected):
        def mock_hood_get_order(oid):
            match oid:
                case canned._SELL_CALL_OID:
                    return sell_call_o
                case canned._SELL_PUT_OID:
                    return sell_put_o

        monkeypatch.setattr(play_close.hood, "get_order_by_id", mock_hood_get_order)
        monkeypatch.setattr(pc, "eject", lambda o: None)

        # should probably not be handled here ... ?
        pc.strangle.sync_orders(pc.strangle.sell_call_o, pc.strangle.sell_put_o)
        assert pc.close_if_filled() == expected

    def test_leg_won_strangle_closed(self, pc, monkeypatch):
        def mock_hood_get_order(oid):
            match oid:
                case canned._SELL_CALL_OID:
                    return canned.sell_call_filled()
                case canned._SELL_PUT_OID:
                    return canned.sell_put_filled()

        monkeypatch.setattr(play_close.hood, "get_order_by_id", mock_hood_get_order)

        assert pc.strangle.state == play_close.strangle._STATE_ACTIVE
        assert pc.confirm_sells_filled()
        assert pc.strangle.state == play_close.strangle._STATE_CLOSED

    # 2. Time expired scenario
    def test_close_time_expired(self, pc, monkeypatch):
        def mock_hood_get_order(oid):
            match oid:
                case canned._SELL_CALL_OID:
                    return canned.sell_call_filled()
                case canned._SELL_PUT_OID:
                    return canned.sell_put_filled()

        def mock_hood_sell_to_close(oid, _):
            match oid:
                case canned._EJECT_CALL_OID:
                    return canned.eject_call_filled()
                case canned._EJECT_PUT_OID:
                    return canned.eject_put_filled()

        monkeypatch.setattr(play_close.hood, "get_order_by_id", mock_hood_get_order)
        monkeypatch.setattr(play_close.hood, "cancel_order", lambda oid: True)
        monkeypatch.setattr(
            play_close.hood,
            "get_option_chain_by_strike",
            lambda ticker, expr, strike_price: canned.option_chain(),
        )
        monkeypatch.setattr(play_close.hood, "sell_to_close", mock_hood_sell_to_close)

        assert pc.eject_chain(pc.strangle.sell_call_o)
        assert pc.eject_chain(pc.strangle.sell_put_o)

        assert pc.strangle.state == play_close.strangle._STATE_ACTIVE
        pc.close_time_expired()
        assert pc.strangle.state == play_close.strangle._STATE_CLOSED
        assert pc.strangle.result == "ejected"
