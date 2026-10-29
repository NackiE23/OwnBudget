from django.apps import AppConfig


class ExpensesConfig(AppConfig):
    """Повсякденні витрати (OwnExpenses). Поки порожній: описується окремим ТЗ."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "expenses"
    verbose_name = "Витрати"
