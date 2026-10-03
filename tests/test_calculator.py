"""Unit tests for the calculator: real arithmetic, and nothing but arithmetic."""
import pytest

from calculator import CalculationError, evaluate, format_result


def test_growth_rate():
    assert round(evaluate("(416161000000 - 391035000000) / 391035000000 * 100"), 4) == 6.4255


def test_thousands_separators_but_not_argument_commas():
    assert evaluate("416,161,000 / 1,000") == 416161
    assert evaluate("min(1, 2)") == 1
    assert evaluate("min(1,2)") == 1


def test_functions_and_precedence():
    assert evaluate("abs(-3) + 2 ** 3 * 2") == 19
    assert evaluate("round(2 / 3, 2)") == 0.67
    assert evaluate("sqrt(16)") == 4


@pytest.mark.parametrize("expression", [
    "__import__('os').system('ls')",
    "open('/etc/passwd')",
    "(1).__class__",
    "x + 1",
    "[1, 2]",
    "'a' * 3",
    "lambda: 1",
    "round(1, ndigits=2)",
])
def test_anything_but_arithmetic_is_rejected(expression):
    with pytest.raises(CalculationError):
        evaluate(expression)


@pytest.mark.parametrize("expression", ["1 / 0", "10 ** 10 ** 10", "2 ** 5000.5", "sqrt(-1)", "1 +"])
def test_uncomputable_is_an_error_not_a_crash(expression):
    with pytest.raises(CalculationError):
        evaluate(expression)


def test_long_expression_rejected():
    with pytest.raises(CalculationError):
        evaluate("1+" * 200 + "1")


def test_format_result():
    assert format_result(6.42556) == "6.4256"
    assert format_result(25126000000.0) == "25,126,000,000 (25.13B)"
    assert format_result(-3.5) == "-3.5"
    assert format_result(12) == "12"
