from django import template

from expenses.formatting import money as format_money

register = template.Library()


@register.filter
def money(value) -> str:
    """Сума з пробілами між тисячами: 12 500.00."""
    return format_money(value)
