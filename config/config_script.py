# pylint: skip-file
# hack to be able to run scripts from scripts dir
import os
import yaml


def parse_yaml():
    with open("../config/settings.yml", "r") as f:
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


conf = DictAsMember(parse_yaml())

yml = parse_yaml()

version = yml["version"]
test_mode_version = f"{version}_TEST_MODE"
discord_webhooks = yml["discord_webhooks"]

redis_host = yml["redis"]["host"]
redis_port = yml["redis"]["port"]
os.environ["REDIS_OM_URL"] = f"redis://@{redis_host}:{redis_port}"

from redis_om.connections import get_redis_connection

redis = get_redis_connection()
