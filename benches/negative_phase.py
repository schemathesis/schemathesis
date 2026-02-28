import pathlib
import sys

import pytest
from hypothesis import HealthCheck, Phase, Verbosity, given, seed, settings

import schemathesis
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis import setup

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent))

from corpus.tools import load_from_corpus, read_corpus_file  # noqa: E402

setup()


def _load_op(corpus_name, schema_file, path, method):
    schema_dict = load_from_corpus(schema_file, read_corpus_file(corpus_name))
    schema = schemathesis.openapi.from_dict(schema_dict)
    schema.config.phases.update(phases=["fuzzing"])
    return schema[path][method]


STRIPE_POST_OP = _load_op("openapi-3.0", "stripe.com/2022-11-15.json", "/v1/payment_intents", "POST")
OPENAI_CHAT_OP = _load_op("openapi-3.1", "openai.com/2.3.0.json", "/chat/completions", "POST")


def _run_negative_benchmark(benchmark, op):
    strategy = op.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    def _run():
        @given(strategy)
        @seed(0)
        @settings(
            max_examples=3,
            database=None,
            deadline=None,
            verbosity=Verbosity.quiet,
            phases=(Phase.generate,),
            suppress_health_check=list(HealthCheck),
        )
        def inner(_case):
            pass

        inner()

    benchmark(_run)


@pytest.mark.benchmark(group="negative-strategy-large-spec")
def test_negative_strategy_large_spec(benchmark):
    _run_negative_benchmark(benchmark, STRIPE_POST_OP)


@pytest.mark.benchmark(group="negative-strategy-openai-chat")
def test_negative_strategy_openai_chat(benchmark):
    _run_negative_benchmark(benchmark, OPENAI_CHAT_OP)
