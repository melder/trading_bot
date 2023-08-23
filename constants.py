import os
import google.auth.transport.requests as greq


# Google sheets verride default timeout 120s -> 300s
greq._DEFAULT_TIMEOUT = 300  # pylint: disable=protected-access

# CSVs

AGGREGATE_CSV = "csv/aggregate.csv"
BLACKLIST_CSV = "csv/blacklist.csv"
WEEKLIES_CSV = "csv/weeklies.csv"
MONTHLIES_CSV = "csv/monthlies.csv"

# Defaults

CONSISTENCY_CONSTANT = 3.5 / 0.6745
RANGE_WEIGHTS = [
    81,
    100,
    121,
    144,
    169,
    196,
    225,
    256,
    289,
    324,
    361,
    400,
    441,
]  # (i+9)^2

GS_MAIN_SHEET = "stonks"
GS_CONSTANTS_WORKSHEET = "Constants"
GS_GF_KILL_SWITCH_CELL = "B7"

# Big daddy mode

BIG_DADDY_MODE = os.environ.get("BD_MODE")

# Decorators defaults

HOOD_API_MAX_RETRY_ATTEMPTS = 5
HOOD_API_RETRY_DELAY = 10
