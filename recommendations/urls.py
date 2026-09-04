from django.urls import path

from . import views

app_name = "recommendations"

urlpatterns = [
    path("<uuid:token>/<str:action>/", views.feedback_view, name="feedback"),
]
