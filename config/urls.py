from django.contrib import admin
from django.urls import include, path

from campaigns.views import run_scheduled

from .health import healthz

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("tasks/run-scheduled/", run_scheduled, name="run-scheduled"),
    path("admin/", admin.site.urls),
    path("r/", include("recommendations.urls")),
    path("", include("readers.urls")),
]
