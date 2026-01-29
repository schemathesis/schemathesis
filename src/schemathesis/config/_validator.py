import json
from pathlib import Path

import jsonschema_rs

with (Path(__file__).absolute().parent / "schema.json").open() as fd:
    CONFIG_SCHEMA = json.loads(fd.read())

CONFIG_VALIDATOR = jsonschema_rs.Draft202012Validator(CONFIG_SCHEMA)
