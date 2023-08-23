from config import config  # pylint: disable=wrong-import-order

import json
import os
import random
import time
from datetime import datetime
from urllib import request

from pytz import timezone


_WEBHOOK_URL = config.discord_webhooks["logger_url"]
_WEBHOOK_URL_BOT_PLAYS = config.discord_webhooks["notifications_url"]

_WEBHOOK_HEADERS = {
    "User-Agent": "PostmanRuntime/7.28.4",
    "Content-Type": "application/json",
}


def debug(msg):
    return send_notification(f"DEBUG - {msg}")


def info(msg):
    return send_notification(f"INFO  - {msg}")


def warn(msg):
    return send_notification(f"WARN  - {msg}")


def error(msg):
    return send_notification(f"ERROR - {msg}")


def fatal(msg):
    return send_notification(f"FATAL - {msg}")


def send_notification(msg, test_mode=False):
    if test_mode or os.environ.get(config.test_mode_version):
        return None
    print(msg)
    t = datetime.now(timezone("US/Eastern")).isoformat(sep=" ")[:24]
    d = {"content": f"```({config.version}) [{t}] {msg}```"}
    data = str(json.dumps(d)).encode("utf-8")

    res = None
    for _ in range(5):
        try:
            req = request.Request(_WEBHOOK_URL, headers=_WEBHOOK_HEADERS, data=data)
            res = request.urlopen(req)
            break
        except Exception:
            time.sleep(random.randint(1, 5))

    return res


def send_bot_play(msg, test_mode=False):
    if test_mode or os.environ.get(config.test_mode_version):
        return None
        return send_notification(f"TEST MODE - {msg}")
    t = datetime.now(timezone("US/Eastern")).isoformat(sep=" ")[:24]
    d = {"content": f"```({config.version}) [{t}] {msg}```"}
    data = str(json.dumps(d)).encode("utf-8")

    res = None
    for _ in range(20):
        try:
            req = request.Request(
                _WEBHOOK_URL_BOT_PLAYS, headers=_WEBHOOK_HEADERS, data=data
            )
            res = request.urlopen(req)
            break
        except Exception:
            time.sleep(random.randint(1, 5))

    return res
