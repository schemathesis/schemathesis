from __future__ import annotations

from enum import Enum
from typing import Any, NoReturn

import click

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.registries import Registry


class CustomHelpMessageChoice(click.Choice):
    """Allows you to customize how choices are displayed in the help message."""

    def __init__(self, *args: Any, choices_repr: str, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.choices_repr = choices_repr

    def get_metavar(self, param: click.Parameter) -> str:
        return self.choices_repr


class BaseCsvChoice(click.Choice):
    def parse_value(self, value: str) -> tuple[list[str], set[str]]:
        selected = [item for item in value.split(",") if item]
        invalid_options = set(selected) - set(self.choices)
        return selected, invalid_options

    def fail_on_invalid_options(self, invalid_options: set[str], selected: list[str]) -> NoReturn:  # type: ignore[misc]
        # Sort to keep the error output consistent with the passed values
        sorted_options = ", ".join(sorted(invalid_options, key=selected.index))
        available_options = ", ".join(self.choices)
        self.fail(f"invalid choice(s): {sorted_options}. Choose from {available_options}.")


class CsvChoice(BaseCsvChoice):
    def convert(  # type: ignore[return]
        self, value: str, param: click.core.Parameter | None, ctx: click.core.Context | None
    ) -> list[str]:
        selected, invalid_options = self.parse_value(value)
        if not invalid_options and selected:
            return selected
        self.fail_on_invalid_options(invalid_options, selected)


class CsvEnumChoice(BaseCsvChoice):
    def __init__(self, choices: type[Enum]):
        self.enum = choices
        super().__init__(tuple(el.name for el in choices))

    def convert(  # type: ignore[return]
        self, value: str, param: click.core.Parameter | None, ctx: click.core.Context | None
    ) -> list[Enum]:
        selected, invalid_options = self.parse_value(value)
        if not invalid_options and selected:
            return [self.enum[item] for item in selected]
        self.fail_on_invalid_options(invalid_options, selected)


class CsvListChoice(click.ParamType):
    def convert(  # type: ignore[return]
        self, value: str, param: click.core.Parameter | None, ctx: click.core.Context | None
    ) -> list[str]:
        return [item for item in value.split(",") if item]


class RegistryChoice(BaseCsvChoice):
    def __init__(self, registry: Registry, with_all: bool = False) -> None:
        self.registry = registry
        self.case_sensitive = True
        self.with_all = with_all

    @property
    def choices(self) -> list[str]:
        choices = self.registry.get_all_names()
        if self.with_all:
            choices.append("all")
        return choices

    def convert(  # type: ignore[return]
        self, value: str, param: click.core.Parameter | None, ctx: click.core.Context | None
    ) -> list[str]:
        selected, invalid_options = self.parse_value(value)
        if not invalid_options and selected:
            return selected
        self.fail_on_invalid_options(invalid_options, selected)


class OptionalInt(click.types.IntRange):
    def convert(  # type: ignore
        self, value: str, param: click.core.Parameter | None, ctx: click.core.Context | None
    ) -> int | NotSet:
        if value.lower() == "none":
            return NOT_SET
        try:
            int(value)
            return super().convert(value, param, ctx)
        except ValueError:
            self.fail(f"{value} is not a valid integer or None.", param, ctx)
