"""Select the existing full suite conservatively; never filter workflow events."""

from __future__ import annotations

import os
import subprocess
from pathlib import PurePosixPath


def documentation_only(paths: list[str]) -> bool:
    """Only publication-gated prose/data can omit the suite.

    Report code, raw data and reproduction inputs have additional suite-only
    integrity checks, as does the generated architecture index.
    """
    return bool(paths) and all(
        (
            path.startswith("docs/")
            and not path.startswith("docs/architecture/")
            and PurePosixPath(path).suffix == ".md"
        )
        or (
            path.startswith("reports/")
            and not {"code", "data"}.intersection(PurePosixPath(path).parts[1:-1])
            and PurePosixPath(path).name not in {"reproduce.md", "suite.yaml"}
            and PurePosixPath(path).suffix in {".md", ".json", ".yaml", ".yml", ".png"}
        )
        for path in paths
    )


def full_suite(event: str, base: str, head: str) -> bool:
    if event == "push":
        return False  # Main uses the existing synthetic smoke and publication gates.
    if event != "pull_request":
        return True  # Scheduled/manual coverage is unconditional.
    try:
        paths = (
            subprocess.check_output(
                ["git", "diff", "--no-renames", "--name-only", "-z", base, head, "--"],
            )
            .decode("utf-8")
            .rstrip("\0")
            .split("\0")
        )
    except (subprocess.CalledProcessError, UnicodeDecodeError):
        return True
    return not documentation_only(paths)


if __name__ == "__main__":
    selected = full_suite(
        os.environ["CI_EVENT"], os.environ["CI_BASE"], os.environ["CI_HEAD"]
    )
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"full_suite={str(selected).lower()}\n")
