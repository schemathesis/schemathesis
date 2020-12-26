import json
from pathlib import Path

ROOT_DIR = Path(__file__).parent


def load_config(file_name):
    with open(ROOT_DIR / file_name) as fd:
        return json.load(fd)
