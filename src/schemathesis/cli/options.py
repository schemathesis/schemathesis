from enum import Enum
from typing import Any, List, Optional, Type, Union

import click

from ..types import NotSet


class CustomHelpMessageChoice(click.Choice):
    """Allows you to customize how choices are displayed in the help message."""

    def __init__(self, *args: Any, choices_repr: str, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.choices_repr = choices_repr

    def get_metavar(self, param: click.Parameter) -> str:
        return self.choices_repr


class CSVOption(click.Choice):
    def __init__(self, choices: Type[Enum]):
        self.enum = choices
        super().__init__(tuple(choices.__members__))

    def convert(  # type: ignore[return]
        self, value: str, param: Optional[click.core.Parameter], ctx: Optional[click.core.Context]
    ) -> List[Enum]:
        items = [item for item in value.split(",") if item]
        invalid_options = set(items) - set(self.choices)
        if not invalid_options and items:
            return [self.enum[item] for item in items]
        # Sort to keep the error output consistent with the passed values
        sorted_options = ", ".join(sorted(invalid_options, key=items.index))
        available_options = ", ".join(self.choices)
        self.fail(f"invalid choice(s): {sorted_options}. Choose from {available_options}")


not_set = NotSet()


class OptionalInt(click.types.IntRange):
    def convert(  # type: ignore
        self, value: str, param: Optional[click.core.Parameter], ctx: Optional[click.core.Context]
    ) -> Union[int, NotSet]:
        if value == "None":
            return not_set
        try:
            int(value)
            return super().convert(value, param, ctx)
        except ValueError:
            self.fail("%s is not a valid integer or None" % value, param, ctx)
