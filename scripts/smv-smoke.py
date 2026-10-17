"""Canonical registered smoke, run under the shared Agent OS process boundary."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib/python"))
from sew.smv import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
