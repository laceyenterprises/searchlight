import click


@click.command()
@click.option("--fast", "mode", flag_value="fast", default=True)
@click.option("--safe", "mode", flag_value="safe", default=True)
def cli(mode):
    click.echo(mode)
