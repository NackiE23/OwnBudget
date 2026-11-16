from django.apps import AppConfig


class ExpensesConfig(AppConfig):
    """Повсякденні витрати з застосунку OwnExpenses: сервер приймає їх синхронізацією й лише показує."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "expenses"
    verbose_name = "Витрати"
