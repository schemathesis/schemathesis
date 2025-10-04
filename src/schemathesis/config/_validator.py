from pathlib import Path

import jsonschema.validators

from schemathesis.core import json

with (Path(__file__).absolute().parent / "schema.json").open() as fd:
    CONFIG_SCHEMA = json.loads(fd.read())

CONFIG_VALIDATOR = jsonschema.validators.Draft202012Validator(CONFIG_SCHEMA)
