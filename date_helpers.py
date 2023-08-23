from config import config  # pylint: disable=wrong-import-order

import calendar
import json
from datetime import date, datetime, timedelta
from pprint import pprint as pp  # pylint: disable=unused-import

import dateutil.parser
import numpy as np
import pytz

from helpers import key_join
import hood

r = config.redis

_DEFAULT_TICKER = "AAPL"
_DEFAULT_TICKER_DAILIES = "SPY"
_TIMEZONE = pytz.timezone("US/Eastern")
_NORMAL_DAILY_MARKET_SECONDS = 23400


#####################################
# EXPIRATION DATES CACHING WEEKLIES #
#####################################

_NS_EXPR_DATES = "expr_dates"
_EXPR_DATES_SET_KEY = key_join(_NS_EXPR_DATES, "unexpired")
_EXPR_DATES_ALL_SET_KEY = key_join(_NS_EXPR_DATES, "all")
_EXPR_DATES_FETCH_FLAG_KEY = key_join(_NS_EXPR_DATES, "api_fetch_flag")
_EXPR_DATES_FETCH_TTL = 86400 * 7


def all_unexpired(force_api=False):
    if (
        force_api
        or not r.get(_EXPR_DATES_FETCH_FLAG_KEY)
        or not (exprs := r.smembers(_EXPR_DATES_SET_KEY))
    ):
        return get_exprs_from_api()["weekly"]
    return list(sorted(exprs))


def all_exprs():
    return list(sorted(r.smembers(_EXPR_DATES_ALL_SET_KEY)))


# month == "01" to "12"
def third_expr_of_month(year, month):
    res = []
    for e in all_exprs():
        _year, _month, _ = e.split("-")
        if month == _month and year == _year:
            res.append(e)
    res.sort()
    return res[2]


def get_exprs_from_api(ticker=_DEFAULT_TICKER, cache=True):
    res = hood.get_chains(ticker)["expiration_dates"]
    if cache:
        r.sadd(_EXPR_DATES_SET_KEY, *res)
        r.sadd(_EXPR_DATES_ALL_SET_KEY, *res)
        r.set(_EXPR_DATES_FETCH_FLAG_KEY, 1, ex=_EXPR_DATES_FETCH_TTL)
    return {"weekly": sorted(res)}


def expire_current_expr():
    r.srem(_EXPR_DATES_SET_KEY, today_date_utc())


def current_expr():
    return all_unexpired()[0]


def next_expr():
    return all_unexpired()[1]


def current_monthly_expr():
    today = datetime.utcnow()
    year = str(today.year)
    month = str(today.month).zfill(2)
    return third_expr_of_month(year, month)


def is_today_an_expr_date():
    return date.today().isoformat() in all_exprs()


def is_this_week_monthly_expr_week():
    return current_expr() == current_monthly_expr()


def is_next_week_monthly_expr_week():
    return next_expr() == current_monthly_expr()


####################################
# EXPIRATION DATES CACHING DAILIES #
####################################

_NS_EXPR_DATES_DAILIES = "expr_dates_day"
_EXPR_DATES_SET_KEY_DAILIES = key_join(_NS_EXPR_DATES_DAILIES, "unexpired")
_EXPR_DATES_ALL_SET_KEY_DAILIES = key_join(_NS_EXPR_DATES_DAILIES, "all")
_EXPR_DATES_FETCH_FLAG_KEY_DAILIES = key_join(_NS_EXPR_DATES_DAILIES, "api_fetch_flag")
_EXPR_DATES_FETCH_TTL_DAILIES = 86400 * 7


def all_unexpired_dailies(force_api=False):
    if (
        force_api
        or not r.get(_EXPR_DATES_FETCH_FLAG_KEY)
        or not (exprs := r.smembers(_EXPR_DATES_SET_KEY_DAILIES))
    ):
        return get_exprs_from_api_dailies()["dailies"]
    return list(sorted(exprs))


def all_exprs_dailies():
    return list(sorted(r.smembers(_EXPR_DATES_ALL_SET_KEY_DAILIES)))


def get_exprs_from_api_dailies(ticker=_DEFAULT_TICKER_DAILIES, cache=True):
    res = hood.get_chains(ticker)["expiration_dates"]
    if cache:
        r.sadd(_EXPR_DATES_SET_KEY_DAILIES, *res)
        r.sadd(_EXPR_DATES_ALL_SET_KEY_DAILIES, *res)
        r.set(_EXPR_DATES_FETCH_FLAG_KEY_DAILIES, 1, ex=_EXPR_DATES_FETCH_TTL_DAILIES)
    return {"dailies": sorted(res)}


def expire_current_expr_dailies():
    r.srem(_EXPR_DATES_SET_KEY_DAILIES, today_date_utc())


def current_expr_dailies():
    return all_unexpired_dailies()[0]


def next_expr_dailies():
    return all_unexpired_dailies()[1]


#######################
# MARKET HOUR CACHING #
#######################

_NS_MARKET_HOURS = "market_hours"


def get_market_hours(iso_date, force_api=False, cache=True):
    if not force_api:
        k = key_join(_NS_MARKET_HOURS, iso_date)
        res = r.get(k)
        if res:
            return json.loads(res)

    res = hood.get_market_hours(iso_date)
    if cache:
        cache_market_hours(iso_date, res)
    return res


def cache_market_hours(iso_date, js):
    k = key_join(_NS_MARKET_HOURS, iso_date)
    res = r.set(k, json.dumps(js))
    return res


def is_market_open_on(iso_date):
    return get_market_hours(iso_date)["is_open"]


def market_opens_at(iso_date):
    if is_market_open_on(iso_date):
        return dateutil.parser.isoparse(get_market_hours(iso_date)["opens_at"])
    return None


def market_closes_at(iso_date):
    if is_market_open_on(iso_date):
        return dateutil.parser.isoparse(get_market_hours(iso_date)["closes_at"])
    return None


def today_market_closes_at():
    return market_closes_at(date.today().isoformat())


def is_market_open_today():
    return get_market_hours(today_date_utc())["opens_at"]


#########################
# MARKET TIME FUNCTIONS #
#########################


def market_days_until_expr(iso_date):
    today = date.today()
    expr_date = date.fromisoformat(iso_date)

    days = 0
    for d in (today + timedelta(n) for n in range((expr_date - today).days)):
        if is_market_open_on(d.isoformat()):
            days += 1

    return days


def market_seconds_in_day(iso_date):
    if not is_market_open_on(iso_date):
        return 0

    res = get_market_hours(iso_date)
    t0 = dateutil.parser.isoparse(res["opens_at"])
    tf = dateutil.parser.isoparse(res["closes_at"])

    if t0 and tf:
        return (tf - t0).seconds

    return 0


def market_seconds_between(dt_from, dt_to):
    dt_from = dt_from.replace(tzinfo=pytz.UTC)
    dt_to = dt_to.replace(tzinfo=pytz.UTC)

    if dt_from > dt_to:
        return 0

    seconds = 0
    open_dt = market_opens_at(dt_from.date().isoformat())
    close_dt = market_closes_at(dt_to.date().isoformat())
    for d in (dt_from + timedelta(n) for n in range((dt_to - dt_from).days + 1)):
        market_seconds = market_seconds_in_day(d.date().isoformat())
        if market_seconds == 0:
            continue
        if d.day == dt_from.day and dt_from > open_dt:
            seconds -= min([market_seconds, (dt_from - open_dt).seconds])
        if d.day == dt_to.day and dt_to < close_dt:
            seconds -= min([market_seconds, (close_dt - dt_to).seconds])
        seconds += market_seconds

    return seconds


def market_seconds_until_expr(iso_date, dt=datetime.utcnow()):
    return market_seconds_between(dt, market_closes_at(iso_date))


def total_market_seconds_in_week_expr(iso_date):
    d0 = datetime.fromisoformat(iso_date) - timedelta(5)
    return market_seconds_until_expr(iso_date, d0)


def is_extra_short_week(iso_date):
    return (
        total_market_seconds_in_week_expr(iso_date) < _NORMAL_DAILY_MARKET_SECONDS * 4
    )


def remaining_market_seconds_to_datetime(seconds, dt_to):
    dt_to = dt_to.replace(tzinfo=pytz.UTC)

    if dt_mc := market_closes_at(dt_to.date().isoformat()):
        seconds += (dt_mc - dt_to).seconds

    while seconds > 0:
        seconds -= market_seconds_in_day(dt_to.date().isoformat())
        dt_to -= timedelta(1)

    dt_to += timedelta(1)
    dt_mo = market_opens_at(dt_to.date().isoformat())

    return dt_mo + timedelta(seconds=-seconds)


def datetime_until_expr_from_market_seconds(seconds, iso_date):
    return remaining_market_seconds_to_datetime(seconds, market_closes_at(iso_date))


def absolute_seconds_between(dt_from, dt_to):
    d0 = dt_from.replace(tzinfo=pytz.UTC)
    df = dt_to.replace(tzinfo=pytz.UTC)
    return (df - d0).total_seconds()


def absolute_seconds_until_expr(iso_date):
    return absolute_seconds_between(datetime.utcnow(), market_closes_at(iso_date))


def seconds_after_market_close(iso_date):
    return (today_datetime_utc() - market_closes_at(iso_date)).total_seconds()


def seconds_before_market_open(iso_date):
    return (market_opens_at(iso_date) - today_datetime_utc()).total_seconds()


def is_market_open_now():
    iso_date = today_date_utc()
    if opens_at := market_opens_at(iso_date):
        return opens_at <= today_datetime_utc() < market_closes_at(iso_date)
    return False


###############
# DATE OUTPUT #
###############


def make_offset_aware(dt, timezone=pytz.UTC):
    return dt.replace(tzinfo=timezone)


def remaining_seconds_to_pretty(seconds, iso_date):
    dtime = remaining_market_seconds_to_datetime(seconds, market_closes_at(iso_date))
    return dt_to_pretty(dtime)


def dt_to_pretty(dt):
    return utc_to_local(dt).strftime("%m/%d/%Y %I:%M:%S%p EST")


def utc_to_local(utc_dt):
    local_dt = utc_dt.replace(tzinfo=pytz.utc).astimezone(_TIMEZONE)
    return _TIMEZONE.normalize(local_dt)


##################
# CALENDAR STUFF #
##################


def week_of_month(iso_date):
    d = datetime.fromisoformat(iso_date)
    x = np.array(calendar.monthcalendar(d.year, d.month))
    return np.where(x == d.day)[0][0] + 1


def today_date_utc():
    return datetime.utcnow().date().isoformat()


def today_datetime_utc():
    now = datetime.utcnow()
    return now.replace(tzinfo=pytz.UTC)
