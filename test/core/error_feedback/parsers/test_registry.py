from __future__ import annotations

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.drf import DRFParser
from schemathesis.core.error_feedback.parsers.jackson import JacksonParser
from schemathesis.core.error_feedback.parsers.pydantic import PydanticParser
from schemathesis.core.error_feedback.parsers.spring import SpringParser


def test_parsers_registry_returns_a_list():
    assert isinstance(PARSERS.get_all(), list)


def test_parsers_registry_contains_spring_parser():
    assert SpringParser in PARSERS.get_all()


def test_parsers_registry_contains_pydantic_parser():
    assert PydanticParser in PARSERS.get_all()


def test_parsers_registry_contains_jackson_parser():
    assert JacksonParser in PARSERS.get_all()


def test_parsers_registry_contains_drf_parser():
    assert DRFParser in PARSERS.get_all()
