# pylint: skip-file
# TODO: migrate constants to config
import os
import yaml


def parse_yaml_vendors():
    with open("config/vendors.yml", "r") as f:
        return yaml.safe_load(f)


def parse_yaml_settings():
    with open("config/settings.yml", "r") as f:
        return yaml.safe_load(f)


class DictAsMember(dict):
    """
    Converts yml to attribute for cleaner access
    """

    def __getattr__(self, name):
        value = self[name]
        if isinstance(value, dict):
            value = DictAsMember(value)
        return value


conf = DictAsMember(parse_yaml_settings() | parse_yaml_vendors())

version = conf.version
test_mode_version = f"{version}_TEST_MODE"
discord_webhooks = conf.discord_webhooks

redis_host = conf.redis.host
redis_port = conf.redis.port
os.environ["REDIS_OM_URL"] = f"redis://@{redis_host}:{redis_port}"

from redis_om.connections import get_redis_connection

redis = get_redis_connection()
