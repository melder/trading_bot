from config import config  # pylint: disable=wrong-import-order

import sys
import csv
import time
from pprint import pprint  # pylint: disable=unused-import
from statistics import mean

import constants
import date_helpers as dh
import hood

conf = config.conf

_SPREAD_SCORE_THRESHOLD = 0.50
_PADDING = 0.10


def read_csv(filename, delimiter="\t"):
    with open(filename, "r", encoding="utf-8") as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=delimiter)
        return list(map(lambda x: x, csv_reader))


def write_to_csv(lines, filename):
    with open(filename, "a", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerows(lines)


def get_weekly_tickers():
    return [t[0] for t in read_csv(constants.WEEKLIES_CSV)]


def get_monthly_tickers():
    return [t[0] for t in read_csv(constants.MONTHLIES_CSV)]


def get_blacklist_tickers():
    return [t[0] for t in read_csv(constants.BLACKLIST_CSV)]


def process_chain(chain, expr, depth=1):
    if not (ticker := chain[0].get("chain_symbol")):
        return None
    if not (price := hood.get_price(ticker)):
        return None

    ivs, oi, vol = [], 0, 0
    ap, bp = 0, 0
    spread_scores = []
    for i, o in enumerate(
        sorted(chain, key=lambda x: abs(float(price) - float(x["strike_price"])))
    ):
        oi += o.get("open_interest") or 0
        vol += o.get("volume") or 0
        if (iv := o.get("implied_volatility")) and i < 4 * depth:
            ivs.append(float(iv))
            ap = o.get("ask_price")
            bp = o.get("bid_price")
            if ap and bp:
                res = (float(ap) - float(bp)) / (float(ap) + _PADDING)
                spread_scores.append(res)

    return {
        "ivs": ivs,
        "oi": oi,
        "vol": vol,
        "price": price,
        "ste": dh.absolute_seconds_until_expr(expr),
        "spread_scores": spread_scores,
    }


def iv_scraper(expr):
    tickers = (
        get_monthly_tickers()
        if not conf.strangle.weeklies_only
        else get_weekly_tickers()
    )
    blacklist = get_blacklist_tickers()
    for ticker in tickers:
        if ticker in blacklist:
            continue

        d[ticker] = {}

        res = []
        for _ in range(5):
            res = hood.condensed_option_chain(ticker, expr)
            if res:
                d[ticker] = process_chain(res, expr)
                print_chain_info(ticker)
                break
            time.sleep(1)

        if x := d[ticker]:
            ss = 0
            if len(x["spread_scores"]) > 0:
                ss = mean(x["spread_scores"])
                if ss > _SPREAD_SCORE_THRESHOLD:
                    continue
            if len(x["ivs"]) > 0:
                x["iv"] = mean(x["ivs"])
                line = [
                    ticker,
                    f"{round(x['iv']*100,2)}%",
                    x["vol"],
                    x["oi"],
                    x["ste"],
                    ss,
                ]
                write_to_csv([line], f"ivs_{expr}.csv")


def print_chain_info(ticker):
    if (x := d[ticker]) and len(x["ivs"]) > 0:
        iv = round(mean(x["ivs"]) * 100, 2)
        oi = x["oi"]
        vol = x["vol"]
        ss = mean(x["spread_scores"]) if len(x["spread_scores"]) > 0 else 1
        print(f"\n\nTicker:\t\t{ticker}")
        print(f"Current Price:\t${round(float(x['price']),2)}")
        print(f"IV:\t\t{iv}%")
        print(f"Open Interest:\t{oi}\nVolume:\t\t{vol}")
        print(f"Spread Score:\t{round(ss,4)}")


d = {}

if __name__ == "__main__":
    start_time = time.time()

    if len(sys.argv) < 2:
        sys.exit("Missing expiration")

    if len(sys.argv) == 2:
        iv_scraper(sys.argv[1])

    print(f"Executed in {((time.time() - start_time)/60)} minutes")
