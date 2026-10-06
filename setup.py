"""Include the unchanged evaluation assets in installed wheels."""

from pathlib import Path
from shutil import copytree

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithAssets(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        for name in ("catalogs", "fixtures", "config", "tasks"):
            copytree(
                root / name,
                Path(self.build_lib) / "sew" / "_data" / name,
                dirs_exist_ok=True,
                ignore=lambda _, names: [
                    name for name in names if name in {"__pycache__", ".pytest_cache"}
                ],
            )


setup(cmdclass={"build_py": BuildWithAssets})
