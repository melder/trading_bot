from random import random

import random
import tests.canned
import redis
import auth


r = redis.Redis(
    host=auth.redis_host(),
    port=auth.redis_port(),
    decode_responses=True
)


def cleanup():
    pass


def failure_chance(_x, _in):
    return _x / _in * 100 <= random.randint(1, 100)
