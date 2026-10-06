import io
import warnings
from unittest.mock import patch
from app import emit


def test_warning_clean_output():
    stream = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        with patch("sys.stdout", stream):
            emit("ready")
    assert stream.getvalue() == "ready\n"
