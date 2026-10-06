import click
import typer


class Port(click.ParamType):
    def convert(self, value, param, ctx):
        try:
            port = int(value)
            if 0 < port < 65536:
                return port
        except (ValueError, TypeError):
            pass
        self.fail("invalid port", param, ctx)


def app():
    cli = typer.Typer()

    @cli.command()
    def serve(port: int = typer.Option(8080, click_type=Port())):
        typer.echo(port)

    return cli
