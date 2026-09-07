import schemathesis


def test_graphql_analysis_checkpoint_returns_false(ctx):
    api = ctx.graphql.apps.books()
    schema = schemathesis.graphql.from_url(api.schema_url)
    assert schema.analysis.checkpoint() is False
