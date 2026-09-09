from django.urls import path

from . import views

app_name = "recommendations"

urlpatterns = [
    # Specific prefixes first: a bare <uuid>/<action>/ would swallow these.
    path("go/<uuid:token>/", views.booking_click_view, name="booking-click"),
    path("c/<uuid:token>/", views.campaign_click_view, name="campaign-click"),
    path("<uuid:token>/<str:action>/", views.feedback_view, name="feedback"),
]
