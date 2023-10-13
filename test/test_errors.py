from schemathesis.exceptions import truncated_json


def test_truncate_simple_dict():
    simple_dict = {"name": "John", "age": 30, "city": "New York"}
    assert (
        truncated_json(simple_dict, max_lines=3, max_width=17)
        == """{
    "name": "J...
    // Output truncated...
}"""
    )
