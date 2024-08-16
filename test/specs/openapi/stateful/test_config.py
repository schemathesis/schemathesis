import hypothesis
import pytest

from schemathesis.stateful.config import (
    _get_default_hypothesis_settings_kwargs,
    _get_hypothesis_settings_kwargs_override,
)


@pytest.mark.parametrize(
    "settings, expected",
    (
        (
            {},
            _get_default_hypothesis_settings_kwargs(),
        ),
        (
            {"phases": [hypothesis.Phase.explicit]},
            {"deadline": None, "stateful_step_count": 6, "suppress_health_check": list(hypothesis.HealthCheck)},
        ),
        (_get_default_hypothesis_settings_kwargs(), {}),
    ),
)
def test_hypothesis_settings(settings, expected):
    assert _get_hypothesis_settings_kwargs_override(hypothesis.settings(**settings)) == expected


def test_create_runner_with_default_hypothesis_settings(runner_factory):
    runner = runner_factory(
        config_kwargs={"hypothesis_settings": hypothesis.settings(**_get_default_hypothesis_settings_kwargs())}
    )
    assert runner.config.hypothesis_settings.deadline is None
