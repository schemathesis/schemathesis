import pytest

import schemathesis
from schemathesis.core.result import Err
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis import setup
from schemathesis.generation.hypothesis.examples import generate_many
from tools.corpus.io import load_from_corpus, read_corpus_file

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

            try:
                generate_many(strategy, 1)
            except Exception:
                pass


@pytest.mark.benchmark(group="multi-operation-sagemaker")
def test_sagemaker_all_operations(benchmark):
    benchmark(_run_all_operations, SAGEMAKER_SCHEMA)
