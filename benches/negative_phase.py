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

CORPUS_OPENAPI_30 = read_corpus_file("openapi-3.0")
STRIPE = load_from_corpus("stripe.com/2022-11-15.json", CORPUS_OPENAPI_30)

STRIPE_NEG = schemathesis.openapi.from_dict(STRIPE)
STRIPE_NEG.config.phases.update(phases=["fuzzing"])

# POST /v1/payment_intents: largest POST body by schema size (~30k bytes, 30 top-level
# properties with deeply nested sub-schemas) — most representative of the profiling scenario
STRIPE_POST_OP = next(
    op.ok()
    for op in STRIPE_NEG.get_all_operations()
    if op.ok().method.upper() == "POST" and op.ok().path == "/v1/payment_intents"
)


@pytest.mark.benchmark
def test_negative_strategy_large_spec(benchmark):
    op = STRIPE_POST_OP
    strategy = op.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    def _run():
        @given(strategy)
        @seed(0)
        @settings(
            max_examples=5,
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
