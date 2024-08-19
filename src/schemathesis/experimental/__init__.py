import os
from dataclasses import dataclass, field

from ..constants import TRUE_VALUES


@dataclass(eq=False)
class Experiment:
    name: str
    verbose_name: str
    env_var: str
    description: str
    discussion_url: str
    _storage: "ExperimentSet" = field(repr=False)

    def enable(self) -> None:
        self._storage.enable(self)

    def disable(self) -> None:
        self._storage.disable(self)

    @property
    def is_enabled(self) -> bool:
        return self._storage.is_enabled(self)

    @property
    def is_env_var_set(self) -> bool:
        return os.getenv(self.env_var, "").lower() in TRUE_VALUES


@dataclass
class ExperimentSet:
    available: set = field(default_factory=set)
    enabled: set = field(default_factory=set)

    def create_experiment(
        self, name: str, verbose_name: str, env_var: str, description: str, discussion_url: str
    ) -> Experiment:
        instance = Experiment(
            name=name,
            verbose_name=verbose_name,
            env_var=f"{ENV_PREFIX}_{env_var}",
            description=description,
            discussion_url=discussion_url,
            _storage=self,
        )
        self.available.add(instance)
        if instance.is_env_var_set:
            self.enable(instance)
        return instance

    def enable(self, feature: Experiment) -> None:
        self.enabled.add(feature)

    def disable(self, feature: Experiment) -> None:
        self.enabled.discard(feature)

    def disable_all(self) -> None:
        self.enabled.clear()

    def is_enabled(self, feature: Experiment) -> bool:
        return feature in self.enabled


ENV_PREFIX = "SCHEMATHESIS_EXPERIMENTAL"
GLOBAL_EXPERIMENTS = ExperimentSet()

OPEN_API_3_1 = GLOBAL_EXPERIMENTS.create_experiment(
    name="openapi-3.1",
    verbose_name="OpenAPI 3.1",
    env_var="OPENAPI_3_1",
    description="Support for response validation",
    discussion_url="https://github.com/schemathesis/schemathesis/discussions/1822",
)
SCHEMA_ANALYSIS = GLOBAL_EXPERIMENTS.create_experiment(
    name="schema-analysis",
    verbose_name="Schema Analysis",
    env_var="SCHEMA_ANALYSIS",
    description="Analyzing API schemas via Schemathesis.io",
    discussion_url="https://github.com/schemathesis/schemathesis/discussions/2056",
)
STATEFUL_TEST_RUNNER = GLOBAL_EXPERIMENTS.create_experiment(
    name="stateful-test-runner",
    verbose_name="New Stateful Test Runner",
    env_var="STATEFUL_TEST_RUNNER",
    description="State machine-based runner for stateful tests in CLI",
    discussion_url="https://github.com/schemathesis/schemathesis/discussions/2262",
)
STATEFUL_ONLY = GLOBAL_EXPERIMENTS.create_experiment(
    name="stateful-only",
    verbose_name="Stateful Only",
    env_var="STATEFUL_ONLY",
    description="Run only stateful tests",
    discussion_url="https://github.com/schemathesis/schemathesis/discussions/2262",
)
COVERAGE_PHASE = GLOBAL_EXPERIMENTS.create_experiment(
    name="coverage-phase",
    verbose_name="Coverage phase",
    env_var="COVERAGE_PHASE",
    description="Generate covering test cases",
    discussion_url="https://github.com/schemathesis/schemathesis/discussions/2418",
)
