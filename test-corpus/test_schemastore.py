from __future__ import annotations

import io
import json
import os
import pathlib
import shutil
import signal
import tarfile
import urllib.request
from typing import Any

import pytest

import schemathesis
from schemathesis.generation.hypothesis import examples

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
CACHE_DIR = CURRENT_DIR / ".schemastore-cache"
SCHEMAS_DIR = CACHE_DIR / "schemas" / "json"
REF_FILE = CACHE_DIR / "REF"
# Pinned upstream commit so XFAIL_SCHEMAS stays meaningful. Override with SCHEMASTORE_REF
# (commit SHA, branch, or tag) when rebaselining against a newer schemastore.
DEFAULT_SCHEMASTORE_REF = "49de45f91f2f8e6be4108d61b44ccd392da0815f"
TARBALL_URL_TEMPLATE = "https://github.com/SchemaStore/schemastore/archive/{ref}.tar.gz"
PER_SCHEMA_TIMEOUT_SECONDS = 20

XFAIL_SCHEMAS: dict[str, str] = {
    "anywork-ac-1.0.json": "external $ref",
    "appsettings.json": ".NET-flavoured regex not valid per JSON Schema",
    "azure-deviceupdate-import-manifest-4.0.json": "external $ref",
    "azure-deviceupdate-import-manifest-5.0.json": "external $ref",
    "azure-deviceupdate-update-manifest-4.json": "external $ref",
    "azure-deviceupdate-update-manifest-5.json": "external $ref",
    "azure-iot-edge-deployment-template-1.0.json": "external $ref",
    "azure-iot-edge-deployment-template-2.0.json": "external $ref",
    "azure-iot-edge-deployment-template-3.0.json": "external $ref",
    "azure-iot-edge-deployment-template-4.0.json": "external $ref",
    "bitrise.json": "external $ref",
    "catalog-info.json": "external $ref",
    "cheatsheets.json": "external $ref",
    "cibuildwheel.json": "external $ref",
    "cinnamon-spice.info.json": "external $ref",
    "clang-format.json": "external $ref",
    "clang-format-18.x.json": "external $ref",
    "clang-format-21.x.json": "external $ref",
    "clangd.json": "external $ref",
    "clasp.json": "external $ref",
    "compilerconfig.json": "external $ref",
    "drone.json": "external $ref",
    "eslintrc.json": "external $ref",
    "feed.json": "external $ref",
    "foundryvtt-base-package-manifest.json": "unsatisfiable",
    "foundryvtt-module-manifest.json": "external $ref",
    "foundryvtt-system-manifest.json": "external $ref",
    "foundryvtt-world-manifest.json": "external $ref",
    "gematik-test-hcpis.json": "external $ref",
    "gematik-test-hcps.json": "external $ref",
    "grunt-clean-task.json": "external $ref",
    "grunt-copy-task.json": "external $ref",
    "grunt-cssmin-task.json": "external $ref",
    "grunt-jshint-task.json": "external $ref",
    "hammerkit.json": "external $ref",
    "hugo.json": "external $ref",
    "jekyll.json": "external $ref",
    "jsbeautifyrc-nested.json": "external $ref",
    "kestra-0.18.0.json": "timeout (>20s)",
    "kestra-0.18.1.json": "timeout (>20s)",
    "kestra-0.18.2.json": "timeout (>20s)",
    "kestra-0.18.3.json": "timeout (>20s)",
    "kestra-0.19.0.json": "timeout (>20s)",
    "lsdlschema.json": "external $ref",
    "minecraft-advancement.json": "external $ref",
    "minecraft-item-modifier.json": "timeout (>20s)",
    "minecraft-pack-mcmeta.json": "external $ref",
    "minecraft-predicate.json": "timeout (>20s)",
    "minecraft-texture-mcmeta.json": "external $ref",
    "mta.json": "external $ref",
    "mtaext.json": "external $ref",
    "openapi-3.X.json": "timeout (>20s)",
    "openapi-arazzo-1.X.json": "unsatisfiable",
    "openapi-overlay-1.X.json": "unsatisfiable",
    "opspec-io-0.1.7.json": "external $ref",
    "package.json": "external $ref",
    "partial-cibuildwheel.json": "external $ref",
    "partial-mypy.json": "external $ref",
    "partial-pdm.json": "external $ref",
    "partial-scikit-build.json": "external $ref",
    "partial-tox.json": "external $ref",
    "pep-723.json": "external $ref",
    "poetry.json": "external $ref",
    "popxf-1.0.json": "unsatisfiable",
    "pre-commit-config.json": "external $ref",
    "prisma.json": "external $ref",
    "problem_package_generators.json": "external $ref",
    "pyproject.json": "external $ref",
    "rancher-fleet-0.5.json": "external $ref",
    "rancher-fleet-0.8.json": "external $ref",
    "rc3-collection-0.0.3.json": "external $ref",
    "rc3-folder-0.0.3.json": "external $ref",
    "rc3-request-0.0.3.json": "external $ref",
    "renovate-39.json": "timeout (>20s)",
    "renovate-40.json": "timeout (>20s)",
    "renovate-41.json": "timeout (>20s)",
    "renovate-42.json": "timeout (>20s)",
    "renovate-global-schema-41.json": "timeout (>20s)",
    "renovate-global-schema-42.json": "timeout (>20s)",
    "renovate-inherited-schema-42.json": "timeout (>20s)",
    "renovate-inherited-schema.json": "timeout (>20s)",
    "renovate.json": "timeout (>20s)",
    "sarif-external-property-file-2.1.0-rtm.0.json": "external $ref",
    "sarif-external-property-file-2.1.0-rtm.1.json": "external $ref",
    "sarif-external-property-file-2.1.0-rtm.2.json": "external $ref",
    "sarif-external-property-file-2.1.0-rtm.3.json": "external $ref",
    "sarif-external-property-file-2.1.0-rtm.4.json": "external $ref",
    "sarif-external-property-file-2.1.0-rtm.5.json": "external $ref",
    "sarif-external-property-file-2.1.0.json": "external $ref",
    "sarif-external-property-file.json": "external $ref",
    "scikit-build.json": "external $ref",
    "schema-draft-v4.json": "required-ref cycle",
    "schema-org-action.json": "external $ref",
    "schema-org-contact-point.json": "external $ref",
    "schema-org-place.json": "external $ref",
    "schema-org-thing.json": "external $ref",
    "setuptools.json": "external $ref",
    "specif-1.0.json": "unsatisfiable",
    "specif-1.1.json": "unsatisfiable",
    "ti8m-cdk-concrete-environment-config.json": "external $ref",
    "ti8m-cdk-concrete-environments.json": "external $ref",
    "toolinfo.1.1.0.json": "external $ref",
    "tox.json": "external $ref",
    "tsoa.json": "external $ref",
    "utcm-monitor.json": "malformed `description: null` in source schema",
    "vega-lite.json": "timeout (>20s)",
    "vega.json": "external $ref",
    "vs-2017.3.host.json": "external $ref",
    "web-manifest-app-info.json": "external $ref",
    "web-manifest-combined.json": "external $ref",
}


def _resolved_ref() -> str:
    return os.environ.get("SCHEMASTORE_REF", DEFAULT_SCHEMASTORE_REF)


def _download_and_extract(ref: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url = TARBALL_URL_TEMPLATE.format(ref=ref)
    with urllib.request.urlopen(url) as response:  # noqa: S310 - hardcoded HTTPS URL
        payload = response.read()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            # Tarball layout: schemastore-<ref>/src/schemas/json/<rel>
            parts = member.name.split("/")
            if len(parts) < 5 or parts[1] != "src" or parts[2] != "schemas" or parts[3] != "json":
                continue
            target_path = CACHE_DIR / "schemas" / "/".join(parts[3:])
            target_path.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            target_path.write_bytes(extracted.read())
    REF_FILE.write_text(ref)


def _ensure_schemastore() -> None:
    desired = _resolved_ref()
    cached = REF_FILE.read_text().strip() if REF_FILE.exists() else None
    if cached == desired and SCHEMAS_DIR.is_dir() and any(SCHEMAS_DIR.iterdir()):
        return
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
    _download_and_extract(desired)
    if not (SCHEMAS_DIR.is_dir() and any(SCHEMAS_DIR.iterdir())):
        raise RuntimeError(f"Schemastore extract produced no files under {SCHEMAS_DIR}")


def _collect_schema_paths() -> list[pathlib.Path]:
    _ensure_schemastore()
    return sorted(SCHEMAS_DIR.rglob("*.json"))


def pytest_generate_tests(metafunc):
    if "schema_path" not in metafunc.fixturenames:
        return
    params = []
    for path in _collect_schema_paths():
        ident = str(path.relative_to(SCHEMAS_DIR))
        marks = []
        reason = XFAIL_SCHEMAS.get(ident)
        if reason is not None:
            marks.append(pytest.mark.xfail(reason=reason, strict=False))
        params.append(pytest.param(path, id=ident, marks=marks))
    metafunc.parametrize("schema_path", params)


def _rewrite_references(node: Any) -> Any:
    if isinstance(node, dict):
        rewritten: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                if value.startswith("#/definitions/"):
                    value = "#/components/schemas/" + value[len("#/definitions/") :]
                elif value.startswith("#/$defs/"):
                    value = "#/components/schemas/" + value[len("#/$defs/") :]
            rewritten[key] = _rewrite_references(value)
        return rewritten
    if isinstance(node, list):
        return [_rewrite_references(item) for item in node]
    return node


def _wrap_as_openapi(schema: dict[str, Any]) -> dict[str, Any]:
    components_schemas: dict[str, Any] = {}
    for source_key in ("definitions", "$defs"):
        block = schema.pop(source_key, None)
        if isinstance(block, dict):
            components_schemas.update(block)
    schema = _rewrite_references(schema)
    components_schemas = _rewrite_references(components_schemas)
    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "schemastore probe", "version": "0.0.1"},
        "paths": {
            "/probe": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    if components_schemas:
        document["components"] = {"schemas": components_schemas}
    return document


class _SchemaGenerationTimeout(Exception):
    pass


def _alarm_handler(signum, frame):
    raise _SchemaGenerationTimeout(f"Generation exceeded {PER_SCHEMA_TIMEOUT_SECONDS}s")


def test_can_draw_example(schema_path: pathlib.Path) -> None:
    schema = json.loads(schema_path.read_bytes())
    if not isinstance(schema, dict):
        pytest.skip("Top-level JSON is not an object schema")
    document = _wrap_as_openapi(schema)
    api = schemathesis.openapi.from_dict(document)
    operation = api["/probe"]["POST"]
    strategy = operation.as_strategy()
    previous = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(PER_SCHEMA_TIMEOUT_SECONDS)
    try:
        examples.generate_one(strategy)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
