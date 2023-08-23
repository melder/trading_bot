import multiprocessing
import os
import sys
import time
import traceback
from pprint import pformat, pprint  # pylint: disable=unused-import

import date_helpers as dh
import decorators
import discord_logging as log  # pylint: disable=unused-import
import iv
import strangler
from models import strangle
from scheduler import jobs
import condorer
import condorer_spy

# Days to expiration to open strangle(s) on
_DTE_MIN = 1
_DTE_MAX = 9
_DTE_RANGE = range(_DTE_MIN, _DTE_MAX + 1)

# Days to expiration to open condor(s) on
_CONDOR_DTE_MIN = 1
_CONDOR_DTE_MAX = 6
_CONDOR_DTE_RANGE = range(_CONDOR_DTE_MIN, _CONDOR_DTE_MAX + 1)


def get_exprs():
    res = []
    for expr in dh.all_unexpired():
        if dh.market_days_until_expr(expr) < _DTE_MIN:
            continue
        if dh.market_days_until_expr(expr) in _DTE_RANGE:
            if dh.is_extra_short_week(expr):
                break
            res.append(expr)
        else:
            break
    return res


def condor_get_exprs():
    res = []
    for expr in dh.all_unexpired():
        if dh.market_days_until_expr(expr) < _CONDOR_DTE_MIN:
            continue
        if dh.market_days_until_expr(expr) in _CONDOR_DTE_RANGE:
            res.append(expr)
        else:
            break
    return res


@decorators.log
def iv_scrape(expr):
    iv.iv_scraper(expr)


@decorators.log
def po_buy(expr):
    strangler.buy(expr)


@decorators.log
def po_open_sells():
    strangler.open_sells()


@decorators.log
def condor_buy(expr):
    condorer.buy(expr)


@decorators.log
def condor_set_sell_limits():
    condorer.sell()


@decorators.log
def condor_close():
    condorer.close()


@decorators.log
def condor_buy_spy():
    condorer_spy.buy(dh.next_expr_dailies())


# spawn process for strangle_open
def spawn_processes(f, _type="strangle"):
    if _type == "strangle":
        exprs = get_exprs()
    elif _type == "condor":
        exprs = condor_get_exprs()
    else:
        log.fatal(f"Invalid type: {_type}")
        raise ValueError("Invalid type")

    procs = []
    for expr in exprs:
        p = multiprocessing.Process(target=f, args=(expr,))
        procs.append(p)
        p.start()
    return procs


# spawn process for close
# vulnerable to race conditions despite mutex
# so temporarily swapping back to synchronous sells
# def spawn_processes_close(f):
#     procs = []
#     for s in strangler.strangle.active_strangles():
#         p = multiprocessing.Process(target=f, args=(s,))
#         procs.append(p)
#         p.start()
#     return procs


if __name__ == "__main__":
    # Necessary to run on linux
    if sys.platform != "darwin":
        multiprocessing.set_start_method("spawn")

    try:
        for j in jobs():
            if not j["active"]:
                continue

            log.info(f"Running job with args: {j}")
            _start = time.perf_counter()

            mod, action = j["module"], j["action"]

            if mod == "strangler":
                if action == "buy":
                    for _p in spawn_processes(po_buy):
                        _p.join()
                if action == "open_sells":
                    po_open_sells()

            if mod == "condorer":
                if action == "buy":
                    for _p in spawn_processes(condor_buy, _type="condor"):
                        _p.join()
                if action == "set_sell_limits":
                    condor_set_sell_limits()
                if action == "sell":
                    condor_close()

            if mod == "condorer_spy":
                if action == "buy":
                    condor_buy_spy()

            if mod == "iv":
                os.system("rm ivs*.csv")
                if action == "run":
                    for _p in spawn_processes(iv_scrape):
                        _p.join()
                if action == "run_condor":
                    for _p in spawn_processes(iv_scrape, _type="condor"):
                        _p.join()

            if mod == "strangle":
                if action == "log_active_strangles":
                    strangler.log_active_strangles()
                if action == "eow_results":
                    strangle.publish_eow_results()

            if mod == "date_helpers":
                if action == "expire_current_expr":
                    dh.expire_current_expr()
                    dh.expire_current_expr_dailies()

            _finish = time.perf_counter()
            log.info(f"Finished in {round(_finish-_start,2)} seconds")

        if dh.is_market_open_now():
            for s in strangle.active_strangles():
                strangler.close_strangle(s)

            # temporarily swapping out due to race condition vulnerability
            # for _p in spawn_processes_close(close_strangle):
            #     _p.join()

    except Exception as err:
        trace = pformat(traceback.format_exception(*sys.exc_info()))
        log.fatal(f"Program crashed:\n\n {pformat(err)}\n\n{trace}")
