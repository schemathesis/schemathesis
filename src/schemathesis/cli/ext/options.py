from __future__ import annotations

from enum import Enum
from typing import Any, NoReturn

import click

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
        selected = [item.strip() for item in value.split(",") if item.strip()]
        if not self.case_sensitive:
            invalid_options = {
                item for item in selected if item.upper() not in {choice.upper() for choice in self.choices}
            }
        else:
            invalid_options = set(selected) - set(self.choices)
        return selected, invalid_options

    def fail_on_invalid_options(self, invalid_options: set[str], selected: list[str]) -> NoReturn:  # type: ignore[misc]
        # Sort to keep the error output consistent with the passed values
        sorted_options = ", ".join(sorted(invalid_options, key=selected.index))
        available_options = ", ".join(self.choices)
        self.fail(f"invalid choice(s): {sorted_options}. Choose from {available_options}.")


class CsvChoice(BaseCsvChoice):
    def convert(self, value: str, param: click.core.Parameter | None, ctx: click.core.Context | None) -> list[str]:
        selected, invalid_options = self.parse_value(value)
        if not invalid_options and selected:
            return selected
        self.fail_on_invalid_options(invalid_options, selected)


class CsvEnumChoice(BaseCsvChoice):
    def __init__(self, choices: type[Enum], case_sensitive: bool = False):
        self.enum = choices
        super().__init__(tuple(el.name.lower() for el in choices), case_sensitive=case_sensitive)

    def convert(self, value: str, param: click.core.Parameter | None, ctx: click.core.Context | None) -> list[Enum]:
        selected, invalid_options = self.parse_value(value)
        if not invalid_options and selected:
            # Match case-insensitively to find the correct enum
            return [
                next(enum_value for enum_value in self.enum if enum_value.value.upper() == item.upper())
                for item in selected
            ]
        self.fail_on_invalid_options(invalid_options, selected)


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

    def convert(self, value: str, param: click.core.Parameter | None, ctx: click.core.Context | None) -> list[str]:
        selected, invalid_options = self.parse_value(value)
        if not invalid_options and selected:
            return selected
        self.fail_on_invalid_options(invalid_options, selected)
