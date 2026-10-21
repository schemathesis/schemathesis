from __future__ import annotations

import pytest

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.ajv import AjvParser
from schemathesis.core.error_feedback.parsers.aspnet import AspNetParser
from schemathesis.core.error_feedback.parsers.confluent import ConfluentParser
from schemathesis.core.error_feedback.parsers.drf import DRFParser
from schemathesis.core.error_feedback.parsers.flask_rest import FlaskRestParser
from schemathesis.core.error_feedback.parsers.go_validator import GoValidatorParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.laravel import LaravelParser
from schemathesis.core.error_feedback.parsers.litestar import LitestarParser
from schemathesis.core.error_feedback.parsers.marshmallow import MarshmallowParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.rails import RailsParser
from schemathesis.core.error_feedback.parsers.restler import RestlerParser
from schemathesis.core.error_feedback.parsers.spring import SpringParser
from schemathesis.core.error_feedback.parsers.symfony import SymfonyParser
from schemathesis.core.error_feedback.parsers.zod import ZodParser

from .helpers import parse_observations
from .test_ajv import _AJV_ACCEPTED_BODIES
from .test_aspnet import _ASPNET_ACCEPTED_BODIES, _ASPNET_REQUIRED
from .test_confluent import _CONFLUENT_ACCEPTED_BODIES
from .test_flask_rest import _FLASK_REST_ACCEPTED_BODIES
from .test_go_validator import _GO_ACCEPTED_BODIES
from .test_laravel import _LARAVEL_ACCEPTED_BODIES, _LARAVEL_REQUIRED
from .test_litestar import _LITESTAR_ACCEPTED_BODIES
from .test_marshmallow import _MARSHMALLOW_ACCEPTED_BODIES
from .test_rails import _RAILS_ACCEPTED_BODIES
from .test_restler import _RESTLER_MESSAGES, RESTLER_VERSIONS, _restler
from .test_symfony import _SYMFONY_ACCEPTED_BODIES
from .test_zod import _ZOD_ACCEPTED_BODIES, _ZOD_INVALID_STRING_EMAIL

_RESTLER_ACCEPTED_BODIES = [
    pytest.param(_restler(message.values[0], version=version), id=f"{message.id}-{version}")
    for message in _RESTLER_MESSAGES
    for version in RESTLER_VERSIONS
]

# Owner, its accepted bodies, and the lower-priority parsers that may claim some of them too.
_OWNERSHIP = [
    (AjvParser, _AJV_ACCEPTED_BODIES, {DRFParser, SpringParser}),
    (AspNetParser, _ASPNET_ACCEPTED_BODIES, {DRFParser}),
    (ConfluentParser, _CONFLUENT_ACCEPTED_BODIES, set()),
    (FlaskRestParser, _FLASK_REST_ACCEPTED_BODIES, set()),
    (GoValidatorParser, _GO_ACCEPTED_BODIES, {SpringParser}),
    (LaravelParser, _LARAVEL_ACCEPTED_BODIES, {DRFParser}),
    (LitestarParser, _LITESTAR_ACCEPTED_BODIES, set()),
    (MarshmallowParser, _MARSHMALLOW_ACCEPTED_BODIES, {DRFParser}),
    (RailsParser, _RAILS_ACCEPTED_BODIES, {DRFParser}),
    (RestlerParser, _RESTLER_ACCEPTED_BODIES, {DRFParser}),
    (SymfonyParser, _SYMFONY_ACCEPTED_BODIES, set()),
    (ZodParser, _ZOD_ACCEPTED_BODIES, {DRFParser, SpringParser}),
]


def _claimants(body):
    return {parser for parser in PARSERS.get_all() if parser().can_parse(body=body)}


@pytest.mark.parametrize(
    ("owner", "body", "lower_claimants"),
    [
        pytest.param(owner, body.values[0], lower_claimants, id=f"{owner.__name__}-{body.id}")
        for owner, bodies, lower_claimants in _OWNERSHIP
        for body in bodies
    ],
)
def test_accepted_body_is_claimed_by_its_owner_first(owner, body, lower_claimants):
    claimants = _claimants(body)
    assert owner in claimants
    assert claimants - {owner} <= lower_claimants
    assert all(parser.priority < owner.priority for parser in lower_claimants)


@pytest.mark.parametrize(
    ("owner", "other", "body"),
    [
        (AspNetParser, DRFParser, _ASPNET_REQUIRED),
        (LaravelParser, DRFParser, _LARAVEL_REQUIRED),
        (MarshmallowParser, DRFParser, {"username": ["Missing data for required field."]}),
        (RailsParser, DRFParser, {"email": ["can't be blank"], "name": ["is too short (minimum is 2 characters)"]}),
        (ZodParser, SpringParser, _ZOD_INVALID_STRING_EMAIL),
        (ZodParser, DRFParser, _ZOD_INVALID_STRING_EMAIL),
        # Bodies that carry both `keyword` and `code` could match either parser; priority picks AJV.
        (
            AjvParser,
            ZodParser,
            {
                "errors": [
                    {
                        "instancePath": "/email",
                        "keyword": "format",
                        "code": "invalid_string",
                        "path": ["email"],
                        "params": {"format": "email"},
                        "message": 'must match format "email"',
                    }
                ]
            },
        ),
    ],
    ids=["aspnet-drf", "laravel-drf", "marshmallow-drf", "rails-drf", "zod-spring", "zod-drf", "ajv-zod"],
)
def test_priority_settles_a_body_two_parsers_claim(owner, other, body):
    assert owner().can_parse(body=body) is True
    assert other().can_parse(body=body) is True
    assert owner.priority > other.priority


@pytest.mark.parametrize("parser", PARSERS.get_all(), ids=lambda parser: parser.__name__)
def test_parser_ignores_a_detail_only_body(parser, make_operation, case_factory):
    assert parse_observations(parser(), {"detail": "nope"}, make_operation, case_factory) == ()


def test_parsers_registry_lists_every_parser():
    parsers = PARSERS.get_all()
    assert isinstance(parsers, list)
    assert {SpringParser, PydanticParser, JacksonParser, DRFParser} <= set(parsers)
