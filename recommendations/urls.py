from django.urls import path

from . import views

app_name = "recommendations"

urlpatterns = [
    # Specific prefixes first: a bare <uuid>/<action>/ would swallow these.
    path("go/<uuid:token>/", views.booking_click_view, name="booking-click"),
    path("go/<uuid:token>/review/<int:review_id>/", views.review_click_view,
         name="review-click"),
    path("reply/<uuid:token>/", views.reply_view, name="reply"),
    path("c/<uuid:token>/", views.campaign_click_view, name="campaign-click"),
    path("<uuid:token>/helped/<str:answer>/", views.helpful_view, name="helpful"),
    path("<uuid:token>/<str:action>/", views.feedback_view, name="feedback"),
]
