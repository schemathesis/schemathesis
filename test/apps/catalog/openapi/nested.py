from __future__ import annotations

from test.apps.builders import flask_app
from test.apps.fragments import handlers, schemas
from test.apps.runtime import OpenAPIApp


def deep_leaf_bug() -> OpenAPIApp:
    return flask_app(
        schemas.deep_leaf_bug(),
        handlers.register_deep_leaf_bug,
        components={"schemas": schemas.deep_leaf_bug_components()},
    )


def header_constraint_bug() -> OpenAPIApp:
    return flask_app(schemas.header_constraint_bug(), handlers.register_header_constraint_bug)


def one_of_branch_bug() -> OpenAPIApp:
    return flask_app(schemas.one_of_branch_bug(), handlers.register_one_of_branch_bug)


def additional_properties_bug() -> OpenAPIApp:
    return flask_app(schemas.additional_properties_bug(), handlers.register_additional_properties_bug)


def languages_with_codes() -> OpenAPIApp:
    return flask_app(schemas.languages_with_codes(), handlers.register_languages_with_codes)
