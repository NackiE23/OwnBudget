"""
Налаштування проєкту OwnBudget.

Усе, що відрізняється між локальною машиною і хостингом, береться зі змінних
середовища (або з файлу .env у корені проєкту). Приклад у .env.example.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse_lazy
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off")
    SECRET_KEY = "django-insecure-local-development-only"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Нестандартний шлях до адмінки (вимога ТЗ). Без початкового слеша, зі слешем у кінці.
ADMIN_URL = os.environ.get("DJANGO_ADMIN_URL", "admin/").strip("/") + "/"

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "budget",
    "expenses",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uk"
LANGUAGES = [("uk", "Українська")]
TIME_ZONE = "Europe/Kyiv"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAdminUser",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "COERCE_DECIMAL_TO_STRING": True,
}

UNFOLD = {
    "SITE_TITLE": "OwnBudget",
    "SITE_HEADER": "OwnBudget",
    "SITE_SYMBOL": "account_balance_wallet",
    "SHOW_HISTORY": True,
    "SIDEBAR": {
        "show_search": False,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Бюджет",
                "items": [
                    {
                        "title": "Місяці",
                        "icon": "calendar_month",
                        "link": reverse_lazy("admin:budget_month_changelist"),
                    },
                    {
                        "title": "Огляд",
                        "icon": "table_chart",
                        "link": reverse_lazy("admin:budget_month_overview"),
                    },
                    {
                        "title": "Записи накопичень",
                        "icon": "savings",
                        "link": reverse_lazy("admin:budget_savingsentry_changelist"),
                    },
                ],
            },
            {
                "title": "Довідники",
                "collapsible": True,
                "items": [
                    {
                        "title": "Категорії розподілу",
                        "icon": "pie_chart",
                        "link": reverse_lazy("admin:budget_allocationcategory_changelist"),
                    },
                    {
                        "title": "Типи рядків",
                        "icon": "list_alt",
                        "link": reverse_lazy("admin:budget_linetype_changelist"),
                    },
                    {
                        "title": "Призначення накопичень",
                        "icon": "account_balance",
                        "link": reverse_lazy("admin:budget_savingsdestination_changelist"),
                    },
                ],
            },
            {
                "title": "Доступ",
                "collapsible": True,
                "items": [
                    {
                        "title": "Користувачі",
                        "icon": "person",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                    },
                ],
            },
        ],
    },
}
