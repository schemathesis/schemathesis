"""API operation parameters.

These are basic entities, that describe what data could be sent to the API.
"""
from copy import deepcopy
from typing import Any, Generator, Optional

import attr


@attr.s(slots=True)
class Example:
    """A free-form parameter example.

    Optionally named, the value can be anything accepted by the API.
    """

    name: Optional[str] = attr.ib()
    value: Any = attr.ib()


@attr.s(slots=True)
class Parameter:
    """A logically separate parameter bound to a location (e.g. to "query string").

    For example, if the API requires multiple headers to be present, then each header is presented as a separate
    `Parameter` instance.
    """

    # The parameter definition in the language acceptable by the API
    definition: Any = attr.ib()
    # TODO. improve
    # Doesn't make sense for individual parameters, that are part of something bigger
    media_type: Optional[str] = attr.ib(default=None)
    # Whether this parameter is considered as an alternative payload.
    # TODO. expand what it means
    is_alternative: bool = attr.ib(default=False)

    def __attrs_post_init__(self) -> None:
        # Do not use `converter=deepcopy` on the field, due to mypy not detecting type annotations
        self.definition = deepcopy(self.definition)

    def iter_examples(self) -> Generator[Example, None, None]:
        """Iterate over all examples defined for the parameter."""
        raise NotImplementedError

    @property
    def location(self) -> str:
        """Where this parameter is located.

        E.g. "query" or "body"
        """
        raise NotImplementedError

    @property
    def name(self) -> str:
        """Parameter name."""
        raise NotImplementedError

    @property
    def is_required(self) -> bool:
        """Whether the parameter is required for a successful API call."""
        raise NotImplementedError
