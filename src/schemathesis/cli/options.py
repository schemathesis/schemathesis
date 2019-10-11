from enum import Enum
from typing import List, Optional, Type

import click


class CSVOption(click.Choice):
    def __init__(self, choices: Type[Enum]):
        self.enum = choices
        super().__init__(tuple(choices.__members__))

    # NOTE(dd). Return type is fixed in mypy==0.730
    # Remove after upgrading the mypy image
    def convert(  # type: ignore
        self, value: str, param: Optional[click.core.Parameter], ctx: Optional[click.core.Context]
    ) -> List[Enum]:
        items = [item for item in value.split(",") if item]
        invalid_options = set(items) - set(self.choices)
        if not invalid_options and items:
            return [self.enum[item] for item in items]  # type: ignore
        # Sort to keep the error output consistent with the passed values
        sorted_options = ", ".join(sorted(invalid_options, key=items.index))
        available_options = ", ".join(self.choices)
        self.fail(f"invalid choice(s): {sorted_options}. Choose from {available_options}")
