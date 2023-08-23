# pylint: skip-file
import pytest
import strangler
import tests.canned as canned
from pprint import pprint
from models import order, strangle

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


class TestStranglerClose:
    @pytest.fixture()
    def sc_wrapped(self, explicit_test_mode, purge_all_orders):
        s = canned.create_mock_strangle()
        sc = strangler.Close(s)
        sc.strangle.activate()
        yield sc
        sc.strangle.delete()

    # disables decorators
    @pytest.fixture()
    def sc(self, sc_wrapped, unwrap):
        sc_wrapped.close_if_filled = unwrap(sc_wrapped.close_if_filled)
        sc_wrapped.eject = unwrap(sc_wrapped.eject)
        sc_wrapped.close_time_expired = unwrap(sc_wrapped.close_time_expired)
        sc_wrapped.confirm_sells_filled = unwrap(sc_wrapped.confirm_sells_filled)

        bind(sc_wrapped, sc_wrapped.close_if_filled)
        bind(sc_wrapped, sc_wrapped.eject)
        bind(sc_wrapped, sc_wrapped.close_time_expired)
        bind(sc_wrapped, sc_wrapped.confirm_sells_filled)

        return sc_wrapped

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
    def test_close_if_filled(self, sc, monkeypatch, sell_call_o, sell_put_o, expected):
        def mock_hood_get_order(oid):
            match oid:
                case canned._SELL_CALL_OID:
                    return sell_call_o
                case canned._SELL_PUT_OID:
                    return sell_put_o

        monkeypatch.setattr(strangler.hood, "get_order_by_id", mock_hood_get_order)
        monkeypatch.setattr(sc, "eject", lambda o: None)

        # should probably not be handled here ... ?
        sc.strangle.sync_orders(sc.strangle.sell_call_o, sc.strangle.sell_put_o)
        assert sc.close_if_filled() == expected
