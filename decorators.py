import pprint
import time
from functools import wraps

import discord_logging as logger

import constants


def log(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        args_repr = [repr(a) for a in args]
        kwargs_repr = [f"{k}={v!r}" for k, v in kwargs.items()]
        signature = ", ".join(args_repr + kwargs_repr)
        logger.debug(f"{func.__qualname__} called with args {signature}")
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"Exception raised in {func.__name__}. exception: {str(e)}")
            raise e

    return wrapper


def log_api(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        args_repr = [repr(a) for a in args]
        kwargs_repr = [f"{k}={v!r}" for k, v in kwargs.items()]
        signature = ", ".join(args_repr + kwargs_repr)
        try:
            result = func(*args, **kwargs)
            logger.debug(
                f"HOOD API - {func.__qualname__}({signature})\n\n{pprint.pformat(result)}"
            )
            return result
        except Exception as e:
            logger.error(f"Exception raised in {func.__name__}. exception: {str(e)}")
            raise e

    return wrapper


def retry(
    attempts=constants.HOOD_API_MAX_RETRY_ATTEMPTS,
    _delay=constants.HOOD_API_RETRY_DELAY,
    skip_first_delay=True,
):
    def retry_decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            args_repr = [repr(a) for a in args]
            kwargs_repr = [f"{k}={v!r}" for k, v in kwargs.items()]
            signature = ", ".join(args_repr + kwargs_repr)
            for i in range(attempts):
                if i > 0 or (i == 0 and not skip_first_delay):
                    time.sleep(_delay)
                if f := func(*args, **kwargs):
                    return f
            logger.error(f"Exhausted retries for {func.__qualname__}({signature}")
            return None

        return wrapper

    return retry_decorator


def delay(_delay):
    def delay_decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            time.sleep(_delay)
            return func(*args, **kwargs)

        return wrapper

    return delay_decorator
