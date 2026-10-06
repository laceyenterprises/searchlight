from pathlib import Path


def resolve(base, relative):
    return Path(base) / relative
