import pathlib
import sys
from contextlib import suppress

import pytest
from hypothesis import HealthCheck, Phase, given, settings

import schemathesis
from schemathesis.core.result import Err
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis import setup

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent))

from tools.corpus.io import load_from_corpus, read_corpus_file  # noqa: E402

setup()

# Many operations sharing component schemas via `$ref`, unlike `negative_phase.py`'s single operation.
SAGEMAKER_SCHEMA = load_from_corpus("amazonaws.com/sagemaker/2017-07-24.json", read_corpus_file("openapi-3.0"))


OPERATION_LIMIT = 20


def _run_all_operations(schema_dict):
    schema = schemathesis.openapi.from_dict(schema_dict)
    schema.config.update(base_url="http://127.0.0.1:8080/")
    operations = [result.ok() for result in schema.get_all_operations() if not isinstance(result, Err)]
    for operation in operations[:OPERATION_LIMIT]:
        for mode in (GenerationMode.POSITIVE, GenerationMode.NEGATIVE):
            try:
                strategy = operation.as_strategy(generation_mode=mode)
            except Exception:
                continue

            @given(strategy)
            @settings(
                max_examples=1,
                deadline=None,
                phases=[Phase.generate],
                database=None,
                suppress_health_check=list(HealthCheck),
            )
            def exercise(case):
                pass

            with suppress(Exception):
                exercise()


@pytest.mark.benchmark(group="multi-operation-sagemaker")
def test_sagemaker_all_operations(benchmark):
    benchmark(_run_all_operations, SAGEMAKER_SCHEMA)
