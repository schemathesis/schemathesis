import datetime
import json
from pathlib import Path

import jsonschema_rs

from schemathesis.core.jsonschema import make_validator

with (Path(__file__).absolute().parent / "schema.json").open() as fd:
    CONFIG_SCHEMA = json.loads(fd.read())

CONFIG_VALIDATOR = make_validator(CONFIG_SCHEMA, jsonschema_rs.Draft202012Validator)

InstancePath = tuple[str | int, ...]


def without_temporal_values(value: object, path: InstancePath, replaced: dict[InstancePath, object]) -> object:
    """Replace TOML dates and times with `null`, which the validator rejects at their path."""
    if isinstance(value, dict):
        return {key: without_temporal_values(item, (*path, key), replaced) for key, item in value.items()}
    if isinstance(value, list):
        return [without_temporal_values(item, (*path, index), replaced) for index, item in enumerate(value)]
    if isinstance(value, datetime.date | datetime.time):
        replaced[path] = value
        return None
    return value
