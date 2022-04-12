from enum import Enum
from typing import Any, List, NoReturn, Optional, Set, Tuple, Type, Union

import click

from ..types import NotSet


class CustomHelpMessageChoice(click.Choice):
    """Allows you to customize how choices are displayed in the help message."""

    def __init__(self, *args: Any, choices_repr: str, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.choices_repr = choices_repr

    def get_metavar(self, param: click.Parameter) -> str:
        return self.choices_repr


class BaseCsvChoice(click.Choice):
    def parse_value(self, value: str) -> Tuple[List[str], Set[str]]:
        selected = [item for item in value.split(",") if item]
        invalid_options = set(selected) - set(self.choices)
        return selected, invalid_options

    def fail_on_invalid_options(self, invalid_options: Set[str], selected: List[str]) -> NoReturn:
        # Sort to keep the error output consistent with the passed values
        sorted_options = ", ".join(sorted(invalid_options, key=selected.index))
        available_options = ", ".join(self.choices)
        self.fail(f"invalid choice(s): {sorted_options}. Choose from {available_options}")


class CsvEnumChoice(BaseCsvChoice):
    def __init__(self, choices: Type[Enum]):
        self.enum = choices
        super().__init__(tuple(choices.__members__))

    def convert(  # type: ignore[return]
        self, value: str, param: Optional[click.core.Parameter], ctx: Optional[click.core.Context]
    ) -> List[Enum]:
        selected, invalid_options = self.parse_value(value)
        if not invalid_options and selected:
            return [self.enum[item] for item in selected]
        self.fail_on_invalid_options(invalid_options, selected)


class CsvChoice(BaseCsvChoice):
    def convert(
        self, value: str, param: Optional[click.core.Parameter], ctx: Optional[click.core.Context]
    ) -> List[str]:
        selected, invalid_options = self.parse_value(value)
        if not invalid_options and selected:
            return selected
        self.fail_on_invalid_options(invalid_options, selected)


not_set = NotSet()


class OptionalInt(click.types.IntRange):
    def convert(  # type: ignore
        self, value: str, param: Optional[click.core.Parameter], ctx: Optional[click.core.Context]
    ) -> Union[int, NotSet]:
        if value.lower() == "none":
            return not_set
        try:
            int(value)
            return super().convert(value, param, ctx)
        except ValueError:
            self.fail("%s is not a valid integer or None" % value, param, ctx)
