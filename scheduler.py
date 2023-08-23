from datetime import datetime

import date_helpers as dh


def jobs():
    return Scheduler.get_jobs()


class Scheduler:
    """
    Schedule actions on a minute-by-minute granularity basis.
    Provides more flexibility than cron jobs
    """

    # move jobs to yml?
    # higher priority jobs on top
    jobs = [
        {"module": "strangler", "action": "buy", "before_close": 6, "active": False},
        {"module": "iv", "action": "run", "before_close": 18, "active": False},
        {
            "module": "strangler",
            "action": "open_sells",
            "after_open": 4,
            "active": False,
        },
        {
            "module": "strangle",
            "action": "log_active_strangles",
            "every": 60,
            "market_hours": True,
            "active": False,
        },
        {
            "module": "strangle",
            "action": "eow_results",
            "after_close": 5,
            "active": False,
        },
        {
            "module": "date_helpers",
            "action": "expire_current_expr",
            "after_close": 15,
            "active": True,
        },
        {"module": "iv", "action": "run_condor", "before_close": 150, "active": True},
        {"module": "condorer", "action": "buy", "before_close": 135, "active": True},
        {
            "module": "condorer",
            "action": "set_sell_limits",
            "after_open": 1,
            "active": True,
        },
        #{"module": "condorer", "action": "sell", "before_close": 120, "active": True},
        {
            "module": "condorer_spy",
            "action": "buy",
            "before_expr_daily": 391,
            "active": True,
        },
    ]

    @classmethod
    def get_jobs(cls):
        return cls().run()

    def __init__(self, iso_date=None):
        self.today_date_utc = iso_date or dh.today_date_utc()
        self.market_open = dh.is_market_open_on(self.today_date_utc)
        self.opens_at = dh.market_opens_at(self.today_date_utc)
        self.closes_at = dh.market_closes_at(self.today_date_utc)
        self.closes_at_next_daily = dh.market_closes_at(dh.next_expr_dailies())

        self.market_minutes_remaining = -1
        self.market_minutes_elapsed = -1
        self.after_market_minutes_elapsed = -1
        self.before_market_minutes_remaining = -1
        self.market_minutes_remaining_next_daily = -1

        if self.opens_at:
            self.market_minutes_elapsed = int(
                dh.market_seconds_between(self.opens_at, datetime.utcnow()) // 60
            )
            self.market_minutes_remaining = int(
                dh.market_seconds_between(datetime.utcnow(), self.closes_at) // 60
            )
            self.after_market_minutes_elapsed = int(
                max(dh.seconds_after_market_close(self.today_date_utc) // 60, 0)
            )
            self.before_market_minutes_remaining = int(
                max(dh.seconds_before_market_open(self.today_date_utc) // 60, 0)
            )
            self.market_minutes_remaining_next_daily = int(
                dh.market_seconds_between(datetime.utcnow(), self.closes_at_next_daily)
                // 60
            )

    def run(self):
        _jobs = []
        for j in self.jobs:
            if j.get("before_close") == self.market_minutes_remaining:
                _jobs.append(j)
            if j.get("after_close") == self.after_market_minutes_elapsed:
                _jobs.append(j)
            if j.get("after_open") == self.market_minutes_elapsed:
                _jobs.append(j)
            if j.get("before_open") == self.before_market_minutes_remaining:
                _jobs.append(j)
            if j.get("before_expr_daily") == self.market_minutes_remaining_next_daily:
                _jobs.append(j)
            if j.get("market_hours") and dh.is_market_open_now():
                if self.market_minutes_elapsed % j.get("every") == 0:
                    _jobs.append(j)

        return _jobs


if __name__ == "__main__":
    print(jobs())
