from __future__ import annotations

import jsonschema_rs
import pytest

from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.specs.openapi.coverage._schema import CoverageContext
from schemathesis.specs.openapi.formats import get_default_format_strategies
from schemathesis.specs.openapi.patterns import update_quantifier


@pytest.fixture
def ctx_factory():
    def _factory(
        *,
        location: ParameterLocation = ParameterLocation.QUERY,
        generation_modes: list[GenerationMode] | None = None,
        is_required: bool = True,
        allow_extra_parameters: bool = True,
        validator_cls: type[jsonschema_rs.Validator] = jsonschema_rs.Draft4Validator,
    ) -> CoverageContext:
        return CoverageContext(
            root_schema={},
            location=location,
            media_type=None,
            generation_modes=generation_modes,
            is_required=is_required,
            custom_formats=get_default_format_strategies(),
            validator_cls=validator_cls,
            update_pattern=update_quantifier,
            allow_extra_parameters=allow_extra_parameters,
        )

    return _factory


@pytest.fixture
def pctx(ctx_factory):
    return ctx_factory(generation_modes=[GenerationMode.POSITIVE])


@pytest.fixture
def nctx(ctx_factory):
    return ctx_factory(generation_modes=[GenerationMode.NEGATIVE])
