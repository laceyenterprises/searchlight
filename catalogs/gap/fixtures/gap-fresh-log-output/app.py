import click


def emit(message):
    click.get_text_stream("stdout").write(message + "\n")
