import pathlib
import sys

import hypothesis
import pytest
from hypothesis import HealthCheck, Phase, Verbosity

import schemathesis
from schemathesis.internal.copy import fast_deepcopy
from schemathesis.runner import from_schema
from schemathesis.specs.openapi._v2 import iter_operations
from schemathesis.specs.openapi._jsonschema import TransformCache

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parent))
CATALOG_DIR = CURRENT_DIR / "data"

from corpus.tools import read_corpus_file, load_from_corpus  # noqa: E402


CORPUS_OPENAPI_30 = read_corpus_file("openapi-3.0")
CORPUS_SWAGGER_20 = read_corpus_file("swagger-2.0")
# Small size (~2k lines in YAML)
# BBCI = load_from_corpus("bbci.co.uk/1.0.json", CORPUS_OPENAPI_30)
# BBCI_SCHEMA = schemathesis.from_dict(BBCI)
# BBCI_OPERATIONS = list(BBCI_SCHEMA.get_all_operations())
# Medium size (~8k lines in YAML)
# VMWARE = load_from_corpus("vmware.local/vrni/1.0.0.json", CORPUS_OPENAPI_30)
# VMWARE_SCHEMA = schemathesis.from_dict(VMWARE)
# VMWARE_OPERATIONS = list(VMWARE_SCHEMA.get_all_operations())
# Large size (~92k lines in YAML)
# STRIPE = load_from_corpus("stripe.com/2022-11-15.json", CORPUS_OPENAPI_30)
# STRIPE_SCHEMA = schemathesis.from_dict(STRIPE)
# Medium GraphQL schema (~6k lines)
# UNIVERSE = read_json_from_catalog("universe.json")
# UNIVERSE_SCHEMA = schemathesis.graphql.from_dict(UNIVERSE)

APPVEYOR = load_from_corpus("appveyor.com/1.0.0.json", CORPUS_SWAGGER_20)
EVETECH = load_from_corpus("evetech.net/0.8.6.json", CORPUS_SWAGGER_20)
OSISOFT = load_from_corpus("osisoft.com/1.11.1.5383.json", CORPUS_SWAGGER_20)
ML_WEBSERVICES = load_from_corpus("azure.com/machinelearning-webservices/2017-01-01.json", CORPUS_SWAGGER_20)


@pytest.mark.parametrize(
    "raw_schema",
    [APPVEYOR, EVETECH, OSISOFT, ML_WEBSERVICES],
    ids=("appveyor", "evetech", "osisoft", "ml-webservices"),
)
def test_iter_operations_v2(raw_schema, benchmark):
    schema = fast_deepcopy(raw_schema)

    @benchmark
    def bench():
        _ = list(iter_operations(schema, ""))


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "raw_schema, cache",
    [
        (APPVEYOR, TransformCache()),
        (EVETECH, TransformCache()),
        (OSISOFT, TransformCache()),
        (ML_WEBSERVICES, TransformCache()),
    ],
    ids=("appveyor", "evetech", "osisoft", "ml-webservices"),
)
def test_iter_operations_v2_cached(raw_schema, cache):
    _ = list(iter_operations(raw_schema, "", cache=cache))


# @pytest.mark.benchmark
# @pytest.mark.parametrize(
#     "raw_schema, loader",
#     [
#         (BBCI, schemathesis.from_dict),
#         (VMWARE, schemathesis.from_dict),
#         (UNIVERSE, schemathesis.graphql.from_dict),
#     ],
#     ids=("bbci", "vmware", "universe"),
# )
# def test_get_all_operations(raw_schema, loader):
#     schema = loader(raw_schema)
#
#     for _ in schema.get_all_operations():
#         pass
#
#
# @pytest.mark.benchmark
# @pytest.mark.parametrize(
#     "raw_schema, loader",
#     [
#         (BBCI, schemathesis.from_dict),
#         (VMWARE, schemathesis.from_dict),
#         (STRIPE, schemathesis.from_dict),
#         (UNIVERSE, schemathesis.graphql.from_dict),
#     ],
#     ids=("bbci", "vmware", "stripe", "universe"),
# )
# def test_length(raw_schema, loader):
#     schema = loader(raw_schema)
#     _ = len(schema)
#
#
# # Schemas with pre-populated cache
# BBCI_OPERATION_ID = "Get_Categories_"
# BBCI_OPERATION_KEY = ("/categories", "get")
# BBCI_SCHEMA_WITH_OPERATIONS_CACHE = schemathesis.from_dict(BBCI)
# BBCI_SCHEMA_WITH_OPERATIONS_CACHE.get_operation_by_id(BBCI_OPERATION_ID)
# VMWARE_OPERATION_ID = "listProblemEvents"
# VMWARE_OPERATION_KEY = ("/entities/problems", "get")
# VMWARE_SCHEMA_WITH_OPERATIONS_CACHE = schemathesis.from_dict(VMWARE)
# VMWARE_SCHEMA_WITH_OPERATIONS_CACHE.get_operation_by_id(VMWARE_OPERATION_ID)
# UNIVERSE_OPERATION_KEY = ("Query", "manageTickets")
# UNIVERSE_SCHEMA_WITH_OPERATIONS_CACHE = schemathesis.graphql.from_dict(UNIVERSE)
# UNIVERSE_SCHEMA_WITH_OPERATIONS_CACHE[UNIVERSE_OPERATION_KEY[0]][UNIVERSE_OPERATION_KEY[1]]
#
#
# @pytest.mark.benchmark
# @pytest.mark.parametrize(
#     "raw_schema, key, loader",
#     [
#         (BBCI, BBCI_OPERATION_KEY, schemathesis.from_dict),
#         (VMWARE, VMWARE_OPERATION_KEY, schemathesis.from_dict),
#         (UNIVERSE, UNIVERSE_OPERATION_KEY, schemathesis.graphql.from_dict),
#     ],
#     ids=("bbci", "vmware", "universe"),
# )
# def test_get_operation_single(raw_schema, key, loader):
#     schema = loader(raw_schema)
#     current = schema
#     for segment in key:
#         current = current[segment]
#
#
# @pytest.mark.benchmark
# @pytest.mark.parametrize(
#     "schema, key",
#     [
#         (BBCI_SCHEMA_WITH_OPERATIONS_CACHE, BBCI_OPERATION_KEY),
#         (VMWARE_SCHEMA_WITH_OPERATIONS_CACHE, VMWARE_OPERATION_KEY),
#         (UNIVERSE_SCHEMA_WITH_OPERATIONS_CACHE, UNIVERSE_OPERATION_KEY),
#     ],
#     ids=("bbci", "vmware", "universe"),
# )
# def test_get_operation_repeatedly(schema, key):
#     current = schema
#     for segment in key:
#         current = current[segment]
#
#
# @pytest.mark.benchmark
# @pytest.mark.parametrize(
#     "raw_schema, key",
#     [(BBCI, BBCI_OPERATION_ID), (VMWARE, VMWARE_OPERATION_ID)],
#     ids=("bbci", "vmware"),
# )
# def test_get_operation_by_id_single(raw_schema, key):
#     schema = schemathesis.from_dict(raw_schema)
#     _ = schema.get_operation_by_id(key)
#
#
# @pytest.mark.benchmark
# @pytest.mark.parametrize(
#     "schema, key",
#     [
#         (BBCI_SCHEMA_WITH_OPERATIONS_CACHE, BBCI_OPERATION_ID),
#         (VMWARE_SCHEMA_WITH_OPERATIONS_CACHE, VMWARE_OPERATION_ID),
#     ],
#     ids=("bbci", "vmware"),
# )
# def test_get_operation_by_id_repeatedly(schema, key):
#     _ = schema.get_operation_by_id(key)
#
#
# @pytest.mark.benchmark
# @pytest.mark.parametrize(
#     "raw_schema, key",
#     [
#         (BBCI, "#/paths/~1categories/get"),
#         (VMWARE, "#/paths/~1entities~1problems/get"),
#     ],
#     ids=("bbci", "vmware"),
# )
# def test_get_operation_by_reference_single(raw_schema, key):
#     schema = schemathesis.from_dict(raw_schema)
#     _ = schema.get_operation_by_reference(key)
#
#
# @pytest.mark.benchmark
# @pytest.mark.parametrize(
#     "schema, key",
#     [
#         (BBCI_SCHEMA_WITH_OPERATIONS_CACHE, "#/paths/~1categories/get"),
#         (VMWARE_SCHEMA_WITH_OPERATIONS_CACHE, "#/paths/~1entities~1problems/get"),
#     ],
#     ids=("bbci", "vmware"),
# )
# def test_get_operation_by_reference_repeatedly(schema, key):
#     _ = schema.get_operation_by_reference(key)
#
#
# @pytest.mark.benchmark
# def test_events():
#     runner = from_schema(
#         BBCI_SCHEMA,
#         checks=(),
#         count_operations=False,
#         count_links=False,
#         hypothesis_settings=hypothesis.settings(
#             deadline=None,
#             database=None,
#             max_examples=1,
#             derandomize=True,
#             suppress_health_check=list(HealthCheck),
#             phases=[Phase.explicit, Phase.generate],
#             verbosity=Verbosity.quiet,
#         ),
#     )
#     for _ in runner.execute():
#         pass
#
#
# @pytest.mark.benchmark
# @pytest.mark.parametrize("raw_schema", [BBCI, VMWARE, STRIPE], ids=("bbci", "vmware", "stripe"))
# def test_links_count(raw_schema):
#     schema = schemathesis.from_dict(raw_schema)
#
#     _ = schema.links_count
