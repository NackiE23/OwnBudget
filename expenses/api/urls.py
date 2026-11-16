from django.urls import path

from expenses.api import views

urlpatterns = [
    path("sync/", views.SyncView.as_view(), name="expenses-sync"),
]
