from django.conf import settings
from django.contrib import admin
from django.urls import include, path

# Кореневої сторінки немає навмисно: редирект на адмінку розкрив би її нестандартний шлях.
urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/budget/", include("budget.api.urls")),
]
