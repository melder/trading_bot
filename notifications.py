# pylint: disable=line-too-long
import date_helpers as dh
import discord_logging as log
from config import config

condor_params = config.conf.condor


def strangle_open(s):
    most_recent_call = s.most_recent_sell_order("call")
    most_recent_put = s.most_recent_sell_order("put")

    log.send_bot_play(
        f""" -- Strangle Opened --

  Ticker:        ${s.ticker}
  Expiration:    {s.expr}
  Time opened:   {dh.dt_to_pretty(s.created_at)} ({int(s.eject_sec_to_expr)*2} market seconds until expiration)
  Eject time:    {dh.remaining_seconds_to_pretty(s.eject_sec_to_expr, s.expr)} ({int(s.eject_sec_to_expr)} market seconds until expiration)

  Call
  ----
  Strike:        {round(s.buy_call_o.strike_price,2)}C
  Contracts:     {int(s.buy_call_o.processed_quantity)}
  Buy premium:   ${format(s.buy_call_o.processed_premium / s.buy_call_o.processed_quantity / 100, '.2f')}
  Buy:           ${int(s.buy_call_o.processed_premium)}

  Sell premium:  ${format(most_recent_call.price, '.2f')}
  Sell target:   ${int(most_recent_call.premium * most_recent_call.pending_quantity)}

  Put
  ---
  Strike:        {round(s.buy_put_o.strike_price,2)}P
  Contracts:     {int(s.buy_put_o.processed_quantity)}
  Buy premium:   ${format(s.buy_put_o.processed_premium / s.buy_put_o.processed_quantity / 100, '.2f')}
  Buy:           ${int(s.buy_put_o.processed_premium)}

  Sell premium:  ${format(most_recent_put.price, '.2f')}
  Sell target:   ${int(most_recent_put.premium * most_recent_put.pending_quantity)}"""
    )


def buy_orders_filled(call_order, put_order, roi_target, eject_time_ratio):
    ticker = call_order.ticker
    expr = call_order.expr
    created_at = min([call_order.created_at, put_order.created_at])
    seconds_to_expr = dh.market_seconds_until_expr(expr, created_at) * eject_time_ratio

    sell_premium_call = (
        float(call_order.processed_premium) / float(call_order.processed_quantity) / 100
    ) * (2 * (1 + roi_target / 100))
    sell_premium_put = (
        float(put_order.processed_premium) / float(put_order.processed_quantity) / 100
    ) * (2 * (1 + roi_target / 100))

    log.send_bot_play(
        f""" -- Buy orders filled --

  Ticker:        ${ticker}
  Expiration:    {expr}
  Time opened:   {dh.dt_to_pretty(created_at)}
  Eject at:      {dh.remaining_seconds_to_pretty(seconds_to_expr, expr)}
  ROI target:    {round(roi_target,2)}%

  Call
  ----
  Strike:        {round(call_order.strike_price,2)}C
  Contracts:     {int(call_order.processed_quantity)}
  Buy premium:   ${format(call_order.processed_premium / call_order.processed_quantity / 100, '.2f')}
  Buy:           ${int(call_order.processed_premium)}

  Sell premium:  ${format(sell_premium_call, '.2f')}
  Sell target:   ${int(float(format(sell_premium_call, '.2f')) * call_order.processed_quantity * 100)}

  Put
  ---
  Strike:        {round(put_order.strike_price,2)}P
  Contracts:     {int(put_order.processed_quantity)}
  Buy premium:   ${format(put_order.processed_premium / put_order.processed_quantity / 100, '.2f')}
  Buy:           ${int(put_order.processed_premium)}

  Sell premium:  ${format(sell_premium_put, '.2f')}
  Sell target:   ${int(float(format(sell_premium_put, '.2f')) * put_order.processed_quantity * 100)}"""
    )


def strangle_close(s):
    call_sell = int(s.get_sell_processed_premium("call"))
    put_sell = int(s.get_sell_processed_premium("put"))

    cbuy = int(s.buy_call_o.processed_premium)
    pbuy = int(s.buy_put_o.processed_premium)
    profit = (call_sell + put_sell) - (cbuy + pbuy)
    roi = ((call_sell + put_sell) - (cbuy + pbuy)) / (cbuy + pbuy) * 100

    call_sell_premium = (
        s.get_sell_processed_premium("call")
        / s.get_sell_processed_quantity("call")
        / 100
        if s.get_sell_processed_quantity("call") > 0
        else 0.00
    )
    put_sell_premium = (
        s.get_sell_processed_premium("put") / s.get_sell_processed_quantity("put") / 100
        if s.get_sell_processed_quantity("put") > 0
        else 0.00
    )

    sign = ""
    sign_roi = ""
    res = "Tie"
    if profit > 0:
        sign = "+"
        sign_roi = "+"
        res = "Win"
    if profit < 0:
        sign = "-"
        res = "Loss"

    log.send_bot_play(
        f""" -- Strangle Closed --

  Ticker:        ${s.ticker}
  Expiration:    {s.expr}
  Time opened:   {dh.dt_to_pretty(s.created_at)} ({int(s.eject_sec_to_expr)*2} market seconds until expiration)
  Eject time:    {dh.remaining_seconds_to_pretty(s.eject_sec_to_expr, s.expr)} ({int(s.eject_sec_to_expr)} market seconds until expiration)


  Call
  ----
  Strike:        {round(s.buy_call_o.strike_price,2)}C
  Contracts:     {int(s.buy_call_o.processed_quantity)}
  Buy premium:   ${format(s.buy_call_o.processed_premium / s.buy_call_o.processed_quantity / 100, '.2f')}
  Buy:           ${int(s.buy_call_o.processed_premium)}

  Sell premium:  ${format(call_sell_premium, '.2f')}
  Sell:          ${int(s.get_sell_processed_premium("call"))}

  Put
  ---
  Strike:        {round(s.buy_put_o.strike_price,2)}P
  Contracts:     {int(s.buy_put_o.processed_quantity)}
  Buy premium:   ${format(s.buy_put_o.processed_premium / s.buy_put_o.processed_quantity / 100, '.2f')}
  Buy:           ${int(s.buy_put_o.processed_premium)}

  Sell premium:  ${format(put_sell_premium, '.2f')}
  Sell:          ${int(s.get_sell_processed_premium("put"))}

  Summary
  -------
  Result:      {res}

  Total Buy:   ${cbuy + pbuy}
  Total Sell:  ${call_sell + put_sell}

  Profit:     {sign}${abs(profit)}
  ROI:        {sign_roi}{round(roi,2)}%"""
    )


def eow_results_pretty(d):
    profit = d["total_sell"] - d["total_buy"]
    roi = profit / d["total_buy"]
    minus = "-" if profit < 0 else ""
    plus_roi = "+" if profit > 0 else ""

    log.send_bot_play(
        f"""  -- End of week summary for {d['expr']} --

    Total strangles:   {d['fills'] + d['ejects']}

    Strangles filled:  {d['fills']}
    Strangles ejected: {d['ejects']}

    Profit Win/Loss: {d['wins']}-{d['losses']}-{d['draws']}

    Total Buy:   ${d['total_buy']}
    Total Sell:  ${d['total_sell']}
    Profit:      {minus}${abs(profit)}
    ROI:         {plus_roi}{round(roi*100,2)}%
    """
    )


def active_strangle_status(strangles):
    lines = ["Active strangles:\n"]
    for s in strangles:
        lines.append(s.pretty())
    if len(lines) > 0:
        log.debug("\n".join(lines))


def aggregated_stats(_strangles):
    total_buy, total_sell = 0, 0
    wins, losses, draws = 0, 0, 0
    fills, ejects = 0, 0

    for s in _strangles:
        call_buy = int(s.buy_call_o.processed_premium)
        put_buy = int(s.buy_put_o.processed_premium)
        call_sell = int(s.get_sell_processed_premium("call"))
        put_sell = int(s.get_sell_processed_premium("put"))

        profit = (call_sell + put_sell) - (call_buy + put_buy)

        if profit > 0:
            wins += 1
        elif profit < 0:
            losses += 1
        else:
            draws += 1

        if s.result == "ejected":
            ejects += 1
        else:
            fills += 1

        total_buy += call_buy + put_buy
        total_sell += call_sell + put_sell

    return {
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "total_buy": total_buy,
        "total_sell": total_sell,
        "fills": fills,
        "ejects": ejects,
    }


########################
# CONDOR NOTIFICATIONS #
########################


def condor_buy_filled(c):
    collateral = round(c.collateral * 100)
    credit = round(c.credit * 100)

    res = c.o.legs
    strikes = sorted([round(float(x["strike_price"]), 2) for x in res])

    sell_credit = round((c.credit * (100 + c.target_roi) - c.target_roi * c.collateral))

    log.send_bot_play(
        f""" -- Condor Buy Filled --

  Ticker:        ${c.ticker}

  Time opened:   {dh.dt_to_pretty(c.created_at)}
  Expiration:    {dh.dt_to_pretty(dh.market_closes_at(c.expr))}

  Condor range:  {strikes[1]} - {strikes[2]}
  Breakevens:    {round(strikes[1] - credit / 100, 2)} - {round(strikes[2] + credit / 100, 2)}

  Collateral:                ${collateral}
  Enter Credit (Max gain):   ${credit}
  Risk (Max loss):           ${collateral - credit}

  Exit Credit Target:   ${sell_credit}
  Target ROI:           +{round((credit - sell_credit) / (collateral - credit) * 100, 2)}%
 """
    )


def condor_sell_limit_set(c):
    collateral = int(c.collateral * 100)
    credit = int(c.credit * 100)

    res = c.o.legs
    strikes = sorted([round(float(x["strike_price"]), 2) for x in res["legs"]])

    if c.sell_oid:
        res2 = c.sell_o

        log.send_bot_play(
            f""" -- Condor Buy Filled --

  Ticker:        ${c.ticker}

  Time opened:   {dh.dt_to_pretty(c.created_at)}
  Expiration:    {dh.dt_to_pretty(dh.market_closes_at(c.expr))}

  Condor range:  {strikes[1]} - {strikes[2]}
  Breakevens:    {round(strikes[1] - credit / 100, 2)} - {round(strikes[2] + credit / 100, 2)}

  Collateral:                ${collateral}
  Enter Credit (Max gain):   ${credit}
  Risk (Max loss):           ${collateral - credit}

  Exit Credit Target:  ${res2.premium}
  Target ROI:          +{c.target_roi}%
 """
        )


def condor_sell_filled(c, total_loss=False):
    collateral = round(c.collateral * 100)
    credit = round(c.credit * 100)

    res = c.o.legs
    strikes = sorted([round(float(x["strike_price"]), 2) for x in res])

    if total_loss:
        sell_credit = collateral
    else:
        sell_credit = round(c.sell_o.actual_price * 100)

    if credit > sell_credit:
        result = "WIN"
        sign_profit = "+"
        sign_roi = "+"
    else:
        result = "LOSS"
        sign_profit = "-"
        sign_roi = "-"

    log.send_bot_play(
        f""" -- Condor Closed --

  Ticker:        ${c.ticker}

  Time opened:   {dh.dt_to_pretty(c.created_at)}
  Expiration:    {dh.dt_to_pretty(dh.market_closes_at(c.expr))}

  Condor range:  {strikes[1]} - {strikes[2]}
  Breakevens:    {round(strikes[1] - credit / 100, 2)} - {round(strikes[2] + credit / 100, 2)}

  Collateral:                ${collateral}
  Enter Credit (Max gain):   ${credit}
  Risk (Max loss):           ${collateral - credit}

  ----

  Result:        {result}

  Exit credit:   ${sell_credit}
  Total profit:  {sign_profit}${abs(credit - sell_credit)}
  Total ROI:     {sign_roi}{abs(round((credit - sell_credit) / (collateral - credit) * 100, 2))}%
 """
    )


if __name__ == "__main__":
    pass
