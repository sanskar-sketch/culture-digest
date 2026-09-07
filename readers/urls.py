from django.urls import path

from . import views

app_name = "readers"

urlpatterns = [
    path("", views.onboarding_view, name="onboarding"),
    path("preferences/<uuid:token>/", views.preferences_view, name="preferences"),
    path("unsubscribe/<uuid:token>/", views.unsubscribe_view, name="unsubscribe"),
]
