import click
from app import runner


def test_text_capture():
    @click.command()
    def cli():
        click.echo("hello")

    assert runner().invoke(cli).output == "hello\n"
