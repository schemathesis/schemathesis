from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING, Iterator

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Ok, Result
from schemathesis.specification import ApiOperation
from schemathesis.specification.interface import ApiSpecification

if TYPE_CHECKING:
    import graphql


class RootType(Enum):
    QUERY = "query"
    MUTATION = "mutation"


class GraphQlOperationLoader:
    def __init__(self, specification: ApiSpecification) -> None:
        self.specification = specification

    @cached_property
    def schema(self) -> graphql.GraphQLSchema:
        import graphql

        return graphql.build_client_schema(self.specification.data)

    def iter_operations(self) -> Iterator[Result[ApiOperation[GraphQl], InvalidSchema]]:
        for _, type_ in (
            (RootType.QUERY, self.schema.query_type),
            (RootType.MUTATION, self.schema.mutation_type),
        ):
            if type_ is None:
                continue
            for field_name, _ in type_.fields.items():
                yield Ok(
                    ApiOperation(
                        specification=self.specification,
                        label=f"{type_.name}.{field_name}",
                        data=GraphQl(schema=self.schema),
                    )
                )


@dataclass
class GraphQl:
    schema: graphql.GraphQLSchema

    __slots__ = ("schema",)
