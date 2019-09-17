def test_path(case_factory):
    case = case_factory(path="/users/{name}", path_parameters={"name": "test"})
    assert case.formatted_path == "/users/test"
