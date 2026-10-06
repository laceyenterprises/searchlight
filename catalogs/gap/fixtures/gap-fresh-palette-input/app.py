import click


def preview(text, color):
    try:
        return click.style(text, fg=color)
    except TypeError:
        return text
