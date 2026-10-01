def test_parameter_dictionary_wins_over_values_captured_from_responses(ctx, cli):
    # `GET /languages/` hands `en` to the resource pool; the binding still decides `code`.
    api = ctx.openapi.apps.languages_with_codes()

    cli.run(
        api.schema_url,
        "--phases=fuzzing",
        "--mode=positive",
        "--max-examples=20",
        config={
            "dictionaries": {"spare": {"values": ["fr"]}},
            "parameters": {"path.code": {"dictionary": "spare"}},
        },
    )

    assert {request.path for request in api.requests if request.method == "DELETE"} == {"/languages/fr"}
