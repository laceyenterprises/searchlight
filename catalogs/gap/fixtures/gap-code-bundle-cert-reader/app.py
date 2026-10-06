from pathlib import Path
import tempfile
from requests.utils import extract_zipped_paths


def read_member(path):
    if Path(path).is_file():
        return Path(path).read_bytes()
    extract_zipped_paths(path)
    return (Path(tempfile.gettempdir()) / Path(path).name).read_bytes()
