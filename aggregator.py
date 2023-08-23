from config import config  # pylint: disable=wrong-import-order

import csv
import sys
from pprint import pprint  # pylint: disable=unused-import
from statistics import mean, stdev
from math import ceil
from datetime import datetime

import numpy as np
import gspread

import constants

import date_helpers as dh

conf = config.conf


def read_csv(filename, delimiter="\t"):
    with open(filename, "r", encoding="utf-8") as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=delimiter)
        return list(map(lambda x: x, csv_reader))


def parse_blacklist_csv():
    return [x[0] for x in read_csv(constants.BLACKLIST_CSV)]


def parse_weeklies_csv():
    return [x[0] for x in read_csv(constants.WEEKLIES_CSV)]


def parse_monthlies_csv():
    return [x[0] for x in read_csv(constants.MONTHLIES_CSV)]


def parse_aggregate_csv(tickers, expr_this_week):
    c_prev = None
    t_prev = None
    for row in read_csv(constants.AGGREGATE_CSV):
        ticker, timestamp = row[0], int(row[1])
        hi, lo, vw = float(row[3]), float(row[4]), float(row[6])
        if c_prev and ticker == t_prev and not expr_this_week:
            hi = max([hi, c_prev])
            lo = min([lo, c_prev])
        weekly_range = (hi - lo) / vw * 100
        if not ticker in tickers:
            continue
        if ticker not in d:
            d[ticker] = {}
            d[ticker]["ranges"] = {}
        d[ticker]["ranges"][timestamp] = weekly_range
        c_prev = float(row[5])
        t_prev = ticker


def parse_ivs_csv(ivs_csv):
    for row in read_csv(ivs_csv):
        ticker, iv, vol, oi, ste, ss = (
            row[0],
            float(row[1][0:-1]),
            row[2],
            row[3],
            float(row[4]),
            float(row[5]),
        )
        if ticker in d:
            d[ticker]["iv"] = iv
            d[ticker]["volume"] = vol
            d[ticker]["oi"] = oi
            d[ticker]["ste"] = ste
            d[ticker]["ss"] = f"{round(ss*100,2)}%"


def remove_range_outliers():
    for t, _ in d.items():
        d[t]["ranges_no_outliers"] = reject_outliers(t)


def reject_outliers(ticker, m=constants.CONSISTENCY_CONSTANT):
    ranges = get_ranges(ticker, with_timestamps=True)

    data = np.array(list(ranges.values()))
    x = np.abs(data - np.median(data))
    mdev = np.median(x)
    s = x / mdev if mdev else 0.0

    ix = np.where(s < m)[0].tolist()  # gets indexes
    l = [list(ranges)[i] for i in ix]  # maps indexes to timestamps
    return {k: ranges[k] for k in l}  # returns dict of filtered timestamps


def add_statistics():
    for t, _ in d.items():
        ranges = get_ranges(t)
        d[t]["avg"] = mean(ranges)
        d[t]["stdev"] = stdev(ranges)
        d[t]["ranges_count"] = len(ranges)

        ranges_no_outliers = get_ranges_no_outliers(t)
        d[t]["avg_no_outliers"] = mean(ranges_no_outliers)
        d[t]["stdev_no_outliers"] = stdev(ranges_no_outliers)
        d[t]["ranges_no_outliers_count"] = len(ranges_no_outliers)


def get_ranges(ticker, with_timestamps=False):
    if with_timestamps:
        return d[ticker]["ranges"]
    return list(d[ticker]["ranges"].values())


def get_ranges_no_outliers(ticker, with_timestamps=False):
    if with_timestamps:
        return d[ticker]["ranges_no_outliers"]
    return list(d[ticker]["ranges_no_outliers"].values())


def add_expected_ranges():
    for t, _ in d.items():
        if iv := d[t].get("iv"):
            d[t]["expected_range"] = 2 * iv * (d[t]["ste"] / (365 * 86400)) ** (1 / 2)


def add_weighted_averages(weights=None):
    weights = weights or constants.RANGE_WEIGHTS
    for t, _ in d.items():
        ranges = get_ranges(t, with_timestamps=True)
        ranges_no_outliers = get_ranges_no_outliers(t, with_timestamps=True)

        i, count_r, count_rno, sum_r, sum_rno = 0, 0, 0, 0, 0
        for k1, v1 in ranges.items():
            sum_r += v1 * weights[i]
            count_r += weights[i]
            for k2, v2 in ranges_no_outliers.items():
                if k1 != k2:
                    continue
                sum_rno += v2 * weights[i]
                count_rno += weights[i]
            i += 1

        if count_r > 0:
            d[t]["weighted_average"] = sum_r / count_r
        if count_rno > 0:
            d[t]["weighted_average_no_outliers"] = sum_rno / count_rno


def add_zscores():
    for t, _ in d.items():
        iv = d[t].get("iv")
        if iv:
            diff1 = d[t]["weighted_average"] - d[t]["expected_range"]
            diff2 = d[t]["weighted_average_no_outliers"] - d[t]["expected_range"]
            d[t]["zscore"] = diff1 / d[t]["stdev"]
            d[t]["zscore_no_outliers"] = diff2 / d[t]["stdev_no_outliers"]


def to_csv_row(ticker):
    o = d[ticker]
    if not o.get("iv"):
        return None

    arr = [
        ticker,
        o["iv"],
        o["volume"],
        o["oi"],
        o["ste"],
        o["ss"],
        o["expected_range"],
        o["avg_no_outliers"],
        o["weighted_average_no_outliers"],
        o["stdev_no_outliers"],
        o["zscore_no_outliers"],
        o["avg"],
        o["weighted_average"],
        o["stdev"],
        o["zscore"],
    ]

    return [str(x) for x in arr]


def week_of_month(dt):
    first_day = dt.replace(day=1)
    dom = dt.day
    adjusted_dom = dom + first_day.weekday()
    return int(ceil(adjusted_dom / 7.0))


def expires_this_week(expr):
    return week_of_month(datetime.fromisoformat(expr)) == week_of_month(
        datetime.utcnow()
    )


def top():
    filtered = {k: v for k, v in d.items() if "zscore_no_outliers" in d[k]}
    return sorted(
        filtered.keys(),
        key=lambda item: filtered[item]["zscore_no_outliers"],
        reverse=True,
    )


def upload_to_google_sheets(sheet_name, worksheet="Sheet365", headers=1, resize=True):
    print(f"Uploading to '{sheet_name}' google sheet ...")

    gc = gspread.service_account()
    sheet = gc.open(sheet_name)
    worksheet = sheet.worksheet(worksheet)

    lines = []
    for ticker in top():
        res = to_csv_row(ticker)
        if res:
            lines.append(res)

    print(len(lines))
    # pprint(d)
    if resize:
        cols = 30
        rows = len(lines)
        worksheet.resize(rows + headers, cols)

    batch = []
    for i, row in enumerate(lines, start=headers + 1):
        batch.append({"range": f"A{i}:O{i}", "values": [row]})

    worksheet.batch_update(batch, value_input_option="USER_ENTERED")


def aggregator(expr=None):
    expr = expr or (
        dh.current_expr() if not dh.is_today_an_expr_date() else dh.next_expr()
    )

    if (
        not conf.strangle.weeklies_only
        and dh.current_monthly_expr() == expr
        or dh.current_monthly_expr() == dh.next_expr()
    ):
        all_tickers = parse_monthlies_csv()
    else:
        all_tickers = parse_weeklies_csv()

    tickers = set(all_tickers) - set(parse_blacklist_csv())

    parse_aggregate_csv(list(tickers), expires_this_week(expr))
    parse_ivs_csv(f"ivs_{expr}.csv")
    remove_range_outliers()
    add_statistics()
    add_weighted_averages()
    add_expected_ranges()
    add_zscores()

    # pprint(d)
    return top()


d = {}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Missing expiration")

    if len(sys.argv) == 2:
        aggregator(sys.argv[1])

    upload_to_google_sheets("strangle bot analysis")
