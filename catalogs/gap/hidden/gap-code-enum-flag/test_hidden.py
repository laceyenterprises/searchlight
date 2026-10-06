import click
from app import option


def test_text_contract():
    for argv in ([], ["--fast"]):
        cli = click.Command("schedule", params=[option()])
        with cli.make_context("schedule", argv) as ctx:
            value = ctx.params["mode"]
            assert type(value) is str
            assert value == "Mode.FAST"
