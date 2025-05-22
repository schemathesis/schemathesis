import pytest

from schemathesis.config import ConfigError
from schemathesis.config._checks import validate_status_codes


@pytest.mark.parametrize(
    ("input_codes", "output", "error"),
    [
        (["200", "404"], ["200", "404"], None),
        (["2xx", "4xx"], ["2xx", "4xx"], None),
        (["200", "2xx", "404", "4xx"], ["200", "2xx", "404", "4xx"], None),
        ([], [], None),
        (["200", "600"], None, "Invalid status code(s): 600"),
        (["2xx", "6xx"], None, "Invalid status code(s): 6xx"),
        (["2xx", "xxx"], None, "Invalid status code(s): xxx"),
        (["2xx", "999"], None, "Invalid status code(s): 999"),
        (["200", "abc"], None, "Invalid status code(s): abc"),
        (["200", "2bc"], None, "Invalid status code(s): 2bc"),
        (["200", "2Xc"], None, "Invalid status code(s): 2Xc"),
        (["200", "20"], None, "Invalid status code(s): 20"),
        (["200", "2xxx"], None, "Invalid status code(s): 2xxx"),
        (["200", "xx"], None, "Invalid status code(s): xx"),
    ],
)
def test_convert_status_codes(input_codes, output, error):
    if error:
        with pytest.raises(ConfigError) as excinfo:
            validate_status_codes(input_codes)
        assert error in str(excinfo.value)
    else:
        assert validate_status_codes(input_codes) == output


def test_convert_status_codes_empty_input():
    assert validate_status_codes(None) is None
