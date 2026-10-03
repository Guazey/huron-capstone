"""Arithmetic done by code, not by the model.

Language models are unreliable at multi-digit arithmetic: a growth rate or
margin worked out "in its head" can be off while looking plausible. The agent
sends the expression here instead, and the answer shows the formula it used.

The expression comes from the model, so it is untrusted input. It is parsed
into a syntax tree and only numbers, + - * / ** %, parentheses, and a few
pure functions are evaluated. eval() is never called, and nothing can reach
names, attributes, imports, or files.
"""
import ast
import math
import operator
import re

MAX_EXPRESSION_CHARS = 300
MAX_EXPONENT = 100  # 10 ** 10 ** 10 would hang the process

BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
}
UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
FUNCTIONS = {"abs": abs, "round": round, "min": min, "max": max, "sqrt": math.sqrt}


class CalculationError(ValueError):
    """The expression isn't plain arithmetic, or can't be computed."""


def _eval(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalculationError("exponent too large")
        return BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
        return UNARY[type(node.op)](_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in FUNCTIONS and not node.keywords):
        return FUNCTIONS[node.func.id](*(_eval(a) for a in node.args))
    raise CalculationError("only numbers, + - * / ** %, parentheses, and abs/round/min/max/sqrt")


def evaluate(expression: str) -> float:
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise CalculationError("expression too long")
    # Numbers copied from tool results can carry thousands separators
    # ("416,161"); argument commas ("min(1, 2)") are left alone.
    cleaned = THOUSANDS_COMMA.sub("", expression)
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError:
        raise CalculationError("not a valid expression") from None
    try:
        result = _eval(tree.body)
    except (ZeroDivisionError, OverflowError, TypeError, ValueError) as e:
        if isinstance(e, CalculationError):
            raise
        raise CalculationError(type(e).__name__) from None
    if not isinstance(result, (int, float)) or not math.isfinite(result):
        raise CalculationError("result is not a finite number")
    return result


def format_result(value: float) -> str:
    """2,345.6789 style: every digit the answer may round from, without float noise."""
    if value == int(value) and abs(value) < 1e15:
        text = f"{int(value):,}"
    else:
        text = f"{value:,.4f}".rstrip("0").rstrip(".")
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= size:
            return f"{text} ({value / size:,.2f}{suffix})"
    return text


if __name__ == "__main__":
    for expr in ("(416161000000 - 391035000000) / 391035000000 * 100",
                 "112,010,000,000 / 416,161,000,000 * 100", "2 ** 0.5", "__import__('os')",
                 "10 ** 10 ** 10", "1 / 0"):
        try:
            print(expr, "=", format_result(evaluate(expr)))
        except CalculationError as e:
            print(expr, "-> rejected:", e)
