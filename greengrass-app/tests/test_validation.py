import pytest

from backend.validation import validate_stack_name


def test_validate_stack_name_accepts_valid_name():
    assert validate_stack_name("DxRuntime-Stack01") == "DxRuntime-Stack01"


@pytest.mark.parametrize(
    "value",
    ["1stack", "stack_name", "stack name", "", "-stack", "a" * 129],
)
def test_validate_stack_name_rejects_invalid(value):
    with pytest.raises(ValueError):
        validate_stack_name(value)
