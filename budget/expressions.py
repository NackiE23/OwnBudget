"""
Арифметичні вирази в сумах, як формули Excel: «=54815+33545»,
«=5000+(2111+4363)+(110.11*45.86)». Вираз зберігається поруч із сумою,
щоб його можна було побачити й відредагувати.
"""

import ast
import operator
import re
from decimal import Decimal, InvalidOperation

from budget.calculations import money

_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


class ExpressionError(ValueError):
    pass


def evaluate(expression: str) -> Decimal:
    """Обчислює арифметичний вираз із чисел і + - * / та дужок. Нічого іншого не виконує."""
    source = expression.strip().lstrip("=").replace(",", ".").replace(" ", "").replace(" ", "")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as error:
        raise ExpressionError(f"Не вираз: «{expression}».") from error
    return _evaluate(tree.body, expression)


def _evaluate(node: ast.AST, expression: str) -> Decimal:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_evaluate(node.operand, expression)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _evaluate(node.left, expression), _evaluate(node.right, expression)
        try:
            return _OPERATORS[type(node.op)](left, right)
        except (ArithmeticError, InvalidOperation) as error:
            raise ExpressionError(f"Не вдалося обчислити «{expression}».") from error
    raise ExpressionError(f"У виразі «{expression}» дозволені лише числа, + - * / і дужки.")


def parse_amount(text: str) -> tuple[Decimal | None, str]:
    """
    Сума з поля вводу: «» → (None, «»), «1 200,50» → (1200.50, «»),
    «=54815+33545» або «54815+33545» → (88360, «=54815+33545»).
    """
    text = text.strip()
    if not text:
        return None, ""
    compact = text.lstrip("=").replace(" ", "").replace(" ", "")
    if re.fullmatch(r"-?\d+(?:[.,]\d+)?", compact):
        return Decimal(compact.replace(",", ".")), ""
    return evaluate(text), "=" + compact.replace(",", ".")


def plain_number(value: Decimal) -> str:
    """113.00 → «113», 110.50 → «110.5»."""
    return format(value.normalize(), "f")


def is_expression(text: str) -> bool:
    """Чи це обчислюваний вираз, а не просто число («=1233» теж лише число)."""
    try:
        value, expression = parse_amount(text)
    except ExpressionError:
        return False
    return bool(expression) and value is not None


def matches(expression: str, value: Decimal | None) -> bool:
    """Чи вираз досі дає цю суму (до копійок)."""
    if not expression or value is None:
        return False
    try:
        return money(evaluate(expression)) == money(value)
    except ExpressionError:
        return False
