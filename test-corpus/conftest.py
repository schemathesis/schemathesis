import json
import warnings

import pytest


def pytest_configure(config):
    warnings.filterwarnings("ignore", category=pytest.PytestDeprecationWarning)


def clean_schema(obj):
    # A helper to display schemas without fields that make too much noise and are irrelevant to dependency analysis
    if isinstance(obj, dict):
        return {k: clean_schema(v) for k, v in obj.items() if k not in ("description", "title", "summary")}
    elif isinstance(obj, list):
        return [clean_schema(item) for item in obj]
    else:
        return obj


@pytest.fixture
def save_schema():
    def save_schema(schema, filename="schema.json"):
        with open(filename, "w") as fd:
            json.dump(clean_schema(schema), fd, indent=4)

    return save_schema
