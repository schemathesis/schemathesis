import graphene


class Patron(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    age = graphene.Int()


class Query(graphene.ObjectType):

    patron = graphene.Field(Patron)

    def resolve_patron(root, info):
        return Patron(id=1, name="Syrus", age=27)


schema = graphene.Schema(query=Query)
