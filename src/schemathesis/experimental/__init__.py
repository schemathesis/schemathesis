from dataclasses import dataclass, field


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
