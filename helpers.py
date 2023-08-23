import importlib


def key_join(*segments, delimiter=":"):
    return delimiter.join([str(x) for x in segments])


def reload(mod):
    importlib.reload(mod)
