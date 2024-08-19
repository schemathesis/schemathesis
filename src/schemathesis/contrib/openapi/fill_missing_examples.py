from __future__ import annotations

from typing import TYPE_CHECKING

from ...hooks import HookContext, register, unregister

if TYPE_CHECKING:
    from ...models import Case


def install() -> None:
    register(before_add_examples)


def uninstall() -> None:
    unregister(before_add_examples)


def before_add_examples(context: HookContext, examples: list[Case]) -> None:
    if not examples and context.operation is not None:
        from ...generation import add_single_example

        strategy = context.operation.as_strategy()
        add_single_example(strategy, examples)
