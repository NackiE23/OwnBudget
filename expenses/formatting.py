from decimal import Decimal


def money(value: Decimal | None) -> str:
    """Сума з пробілами між тисячами, як у бюджеті: 12 500.00."""
    return f"{value or Decimal('0'):,.2f}".replace(",", " ")
