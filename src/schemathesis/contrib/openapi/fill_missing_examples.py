from __future__ import annotations

from schemathesis.generation.case import Case
from schemathesis.hooks import HookContext, register, unregister


def install() -> None:
    register(before_add_examples)


def uninstall() -> None:
    unregister(before_add_examples)


def before_add_examples(context: HookContext, examples: list[Case]) -> None:
    if not examples and context.operation is not None:
        from schemathesis.generation.hypothesis.examples import add_single_example

        strategy = context.operation.as_strategy()
        add_single_example(strategy, examples)
