import click
import os
import sys
from app import runner


def test_descriptor_capture():
    @click.command()
    def cli():
        # fileno remains usable for native consumers, but points at the saved
        # host descriptor. Native stdout writers use descriptor 1 for capture.
        assert sys.stdout.fileno() >= 0
        os.write(1, b"native-output\n")

    r = runner().invoke(cli)
    assert r.exit_code == 0, r.exception
    assert "native-output" in r.output
