from enum import Enum
import click


class Mode(Enum):
    FAST = "fast"


def option():
    return click.Option(["--fast", "mode"], flag_value=Mode.FAST, default=True)
