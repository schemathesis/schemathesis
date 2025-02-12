import os
from dataclasses import dataclass, field

from schemathesis.core import string_to_boolean


@dataclass(eq=False)
class Experiment:
    name: str
    env_var: str
    description: str
    discussion_url: str
    _storage: "ExperimentSet" = field(repr=False)

    @property
    def label(self) -> str:
        return self.name.lower().replace(" ", "-")

    def enable(self) -> None:
        self._storage.enable(self)

    def disable(self) -> None:
        self._storage.disable(self)

    @property
    def is_enabled(self) -> bool:
        return self._storage.is_enabled(self)

    @property
    def is_env_var_set(self) -> bool:
        return string_to_boolean(os.getenv(self.env_var, "")) is True


@dataclass
class ExperimentSet:
    available: set = field(default_factory=set)
    enabled: set = field(default_factory=set)

    def create_experiment(self, name: str, env_var: str, description: str, discussion_url: str) -> Experiment:
        instance = Experiment(
            name=name,
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
POSITIVE_DATA_ACCEPTANCE = GLOBAL_EXPERIMENTS.create_experiment(
    name="Positive Data Acceptance",
    env_var="POSITIVE_DATA_ACCEPTANCE",
    description="Verifying schema-conformant data is accepted",
    discussion_url="https://github.com/schemathesis/schemathesis/discussions/2499",
)
