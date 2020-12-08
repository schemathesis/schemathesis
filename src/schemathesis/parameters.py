"""API operation parameters.

These are basic entities, that describe what data could be sent to the API.
"""
from copy import deepcopy
from typing import Any

import attr


@attr.s(slots=True)
class Parameter:
    """A logically separate parameter bound to a location (e.g. to "query string").

    For example, if the API requires multiple headers to be present, then each header is presented as a separate
    `Parameter` instance.
    """

    # The parameter definition in the language acceptable by the API
    definition: Any = attr.ib()

    def __attrs_post_init__(self) -> None:
        # Do not use `converter=deepcopy` on the field, due to mypy not detecting type annotations
        self.definition = deepcopy(self.definition)

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
