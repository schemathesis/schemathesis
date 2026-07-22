import pytest

from schemathesis.config import ConfigError, SchemathesisConfig
from schemathesis.generation import GenerationMode


@pytest.fixture
def config():
    return SchemathesisConfig().projects.get_default()


def test_unknown_phase_name_rejected(config):
    with pytest.raises(ConfigError, match="Did you mean 'fuzzing'"):
        config.phases.update(phases=["fuzzin"])


def test_known_phase_names_accepted(config):
    config.phases.update(phases=["fuzzing", "coverage"])
    assert (config.phases.fuzzing.enabled, config.phases.coverage.enabled) == (True, True)
    assert (config.phases.examples.enabled, config.phases.stateful.enabled) == (False, False)


def test_generation_modes_accept_strings(config):
    config.generation.update(modes=["positive"])
    # Plain strings compare equal to the enum but lack its attributes, which the engine relies on
    assert config.generation.modes == [GenerationMode.POSITIVE]
    assert [mode.is_positive for mode in config.generation.modes] == [True]


def test_unknown_generation_mode_rejected(config):
    with pytest.raises(ConfigError, match="Did you mean 'positive'"):
        config.generation.update(modes=["positive-only"])


def test_non_integer_max_examples_rejected(config):
    with pytest.raises(ConfigError, match="max-examples"):
        config.generation.update(max_examples="ten")


def test_update_keeps_flags_set_by_earlier_update(config):
    config.generation.update(
        no_shrink=True,
        deterministic=True,
        allow_x00=False,
        allow_extra_parameters=False,
        graphql_allow_null=False,
        unique_inputs=True,
    )
    config.generation.update(max_examples=10)
    assert (
        config.generation.no_shrink,
        config.generation.deterministic,
        config.generation.allow_x00,
        config.generation.allow_extra_parameters,
        config.generation.graphql_allow_null,
        config.generation.unique_inputs,
    ) == (True, True, False, False, False, True)
