import json
from pathlib import Path

import jsonschema.validators

with (Path(__file__).absolute().parent / "schema.json").open() as fd:
    CONFIG_SCHEMA = json.loads(fd.read())

CONFIG_VALIDATOR = jsonschema.validators.Draft202012Validator(CONFIG_SCHEMA)
