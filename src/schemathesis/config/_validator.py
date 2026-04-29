import json
from pathlib import Path

import jsonschema_rs

from schemathesis.core.jsonschema import make_validator

with (Path(__file__).absolute().parent / "schema.json").open() as fd:
    CONFIG_SCHEMA = json.loads(fd.read())

CONFIG_VALIDATOR = make_validator(CONFIG_SCHEMA, jsonschema_rs.Draft202012Validator)
