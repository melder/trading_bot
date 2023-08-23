# pylint: skip-file
import pytest
import play_close
import tests.canned as canned
from pprint import pprint
import order

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


### Teardown - kinda situational so would rather shove in specialized fixture
### Maybe move tests that require teardown to separate file?


def delete_redis_objects(*oids):
    for oid in oids:
        if o := order.find(oid):
            o.o.delete(o.id)


class TestPlayClose:
    @pytest.fixture()
    def purge_all_orders(pc):
        yield pc
        delete_redis_objects(
            canned._BUY_CALL_OID,
            canned._BUY_PUT_OID,
            canned._SELL_CALL_OID,
            canned._SELL_PUT_OID,
        )

    @pytest.fixture()
    def pc_wrapped(self, explicit_test_mode, purge_all_orders):
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

    def test_pc_object_sanity(self, pc):
        s = pc.strangle

        assert pc
        assert s
        assert s.ticker == "SPY"
        assert s.expr == canned._DEFAULT_EXPR
        assert s.buy_call_oid == canned._BUY_CALL_OID
        assert s.buy_put_oid == canned._BUY_PUT_OID
        assert s.sell_call_oid == canned._SELL_CALL_OID
        assert s.sell_put_oid == canned._SELL_PUT_OID
        assert s.state == play_close.strangle._STATE_ACTIVE

    # unit tests
    # 1. cancel_order - done
    # 2. eject_chain - done
    # 3. cancel_and_sell - done (cleanup?)
    # 3a. cancel_and_sell - done
    # 4. eject - done

    def test_cancel_order(self, pc, monkeypatch):
        monkeypatch.setattr(play_close.hood, "cancel_order", lambda o: True)
        call_order = pc.strangle.sell_call_o
        put_order = pc.strangle.sell_put_o
        assert pc.cancel_order(call_order)
        assert pc.cancel_order(put_order)

    def test_get_option_chain(self, pc, monkeypatch):
        monkeypatch.setattr(
            play_close.hood, "get_order_by_id", lambda oid: canned.sell_call_confirmed()
        )

        def map_option_chain_to_strike(ticker, expr, strike):
            option_chains = canned.option_chain()
            for oc in option_chains:
                if float(oc["strike_price"]) == pc.strangle.sell_call_o.strike_price:
                    return [oc]

        monkeypatch.setattr(
            play_close.hood,
            "get_option_chain_by_strike",
            map_option_chain_to_strike,
        )

        sell_call_o = pc.strangle.sell_call_o
        option_chain = pc.eject_chain(sell_call_o)

        assert sell_call_o.ticker == option_chain["symbol"]
        assert sell_call_o.strike_price == float(option_chain["strike_price"])

        sell_put_o = pc.strangle.sell_call_o
        option_chain = pc.eject_chain(sell_put_o)

        assert sell_put_o.ticker == option_chain["symbol"]
        assert sell_put_o.strike_price == float(option_chain["strike_price"])

    def test_cancel_and_sell(self, pc, monkeypatch):
        monkeypatch.setattr(play_close.hood, "cancel_order", lambda o: True)

        def map_option_chain_to_strike(ticker, expr, strike):
            option_chains = canned.option_chain()
            for oc in option_chains:
                if float(oc["strike_price"]) == pc.strangle.sell_call_o.strike_price:
                    return [oc]

        monkeypatch.setattr(
            play_close.hood,
            "get_option_chain_by_strike",
            map_option_chain_to_strike,
        )

        def mock_hood_sell_to_close(o, sell_price):
            return canned.sell_call_confirmed()

        monkeypatch.setattr(play_close.hood, "sell_to_close", mock_hood_sell_to_close)

        res = pc.cancel_and_sell(pc.strangle.sell_call_o)

        assert res == canned.sell_call_confirmed() | canned.min_ticks()

    def test_cancel_and_sell_at_min_price(self, pc, monkeypatch):
        monkeypatch.setattr(
            play_close.hood,
            "get_order_by_id",
            lambda o: canned.sell_call_confirmed_min_tick(),
        )
        pc.strangle.sync_orders(pc.strangle.sell_call_o)
        assert not pc.cancel_and_sell(pc.strangle.sell_call_o)

    @pytest.mark.parametrize(
        "o_type, p0, p1, pp0, pp1, q0, q1",
        [("call", 300, 50, 0, 100, 2, 2), ("put", 600, 10, 0, 10, 1, 1)],
    )
    def test_eject(self, pc, monkeypatch, o_type, p0, p1, pp0, pp1, q0, q1):
        def mock_hood_get_order(oid):
            match oid:
                case canned._EJECT_CALL_OID:
                    return canned.eject_call_filled()
                case canned._EJECT_PUT_OID:
                    return canned.eject_put_filled()

        monkeypatch.setattr(play_close.hood, "get_order_by_id", mock_hood_get_order)

        def mock_cancel_and_sell(o):
            match o.id:
                case canned._EJECT_CALL_OID:
                    return canned.eject_call_filled()
                case canned._EJECT_PUT_OID:
                    return canned.eject_put_filled()

        monkeypatch.setattr(pc, "cancel_and_sell", mock_cancel_and_sell)

        if o_type == "call":
            eject_order = pc.strangle.sell_call_o
        if o_type == "put":
            eject_order = pc.strangle.sell_put_o

        assert float(eject_order.premium) == p0
        assert float(eject_order.processed_premium) == pp0
        assert float(eject_order.quantity) == q0

        pc.eject(eject_order)

        assert float(eject_order.premium) == p1
        assert float(eject_order.processed_premium) == pp1
        assert float(eject_order.quantity) == q1
