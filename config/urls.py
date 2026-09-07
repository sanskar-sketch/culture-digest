from django.contrib import admin
from django.urls import include, path

from .health import healthz

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("r/", include("recommendations.urls")),
    path("", include("readers.urls")),
]
