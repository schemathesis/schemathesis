from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import hypothesis

from schemathesis.core.errors import InvalidSchema
from schemathesis.generation import derive_operation_seed, overrides
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.builder import HypothesisTestConfig, HypothesisTestMode, create_test
from schemathesis.generation.hypothesis.given import GivenInput

if TYPE_CHECKING:
    from schemathesis.config import PhasesConfig, ProjectConfig
    from schemathesis.schemas import APIOperation


def enabled_modes(phases: PhasesConfig) -> list[HypothesisTestMode]:
    modes = []
    if phases.examples.enabled:
        modes.append(HypothesisTestMode.EXAMPLES)
    if phases.fuzzing.enabled:
        modes.append(HypothesisTestMode.FUZZING)
    if phases.coverage.enabled:
        modes.append(HypothesisTestMode.COVERAGE)
    return modes


def build_operation_test(
    *,
    config: ProjectConfig,
    operation: APIOperation,
    test_func: Callable,
    given_kwargs: dict[str, GivenInput],
    feedback: FeedbackSources,
    explicit_settings: hypothesis.settings | None = None,
) -> Callable:
    modes = enabled_modes(config.phases_for(operation=operation))
    # Only fuzzing uses max_examples.
    phase = "fuzzing" if HypothesisTestMode.FUZZING in modes else None
    return create_test(
        operation=operation,
        test_func=test_func,
        config=HypothesisTestConfig(
            settings=config.get_hypothesis_settings(operation=operation, phase=phase),
            explicit_settings=explicit_settings,
            explicit_setting_names=config.explicit_hypothesis_settings(operation=operation, phase=phase),
            modes=modes,
            seed=derive_operation_seed(config.seed, operation.label),
            project=config,
            override=overrides.with_config_headers(config, operation=operation),
            given_kwargs=given_kwargs,
            feedback=feedback,
        ),
    )


def schema_error_suffix(error: InvalidSchema) -> str:
    if error.method:
        return f"[{error.method.upper()} {error.path}]"
    return f"[{error.path}]"
