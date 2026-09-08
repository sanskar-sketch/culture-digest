from django.urls import path

from .views import auth, campaigns, dashboard, interests, listings, newsletters, readers
from .views import settings as settings_views
from .views import templates as template_views

app_name = "desk"

urlpatterns = [
    path("login/", auth.DeskLoginView.as_view(), name="login"),
    path("logout/", auth.DeskLogoutView.as_view(), name="logout"),
    path("password/", auth.DeskPasswordChangeView.as_view(), name="password_change"),
    path("password/done/", auth.DeskPasswordChangeDoneView.as_view(), name="password_change_done"),

    path("", dashboard.dashboard_view, name="dashboard"),

    path("listings/", listings.listing_list, name="listings_list"),
    path("listings/add/", listings.listing_form, name="listings_add"),
    path("listings/<int:pk>/", listings.listing_form, name="listings_change"),

    path("interests/", interests.interest_list, name="interests_list"),
    path("interests/add/", interests.interest_form, name="interests_add"),
    path("interests/<int:pk>/", interests.interest_form, name="interests_change"),

    path("readers/", readers.reader_list, name="readers_list"),
    path("readers/<int:pk>/", readers.reader_form, name="readers_change"),

    path("campaigns/", campaigns.campaign_list, name="campaigns_list"),
    path("campaigns/add/", campaigns.campaign_form, name="campaigns_add"),
    path("campaigns/<int:pk>/", campaigns.campaign_form, name="campaigns_change"),
    path("campaigns/<int:pk>/preview/", campaigns.campaign_preview, name="campaigns_preview"),
    path("campaigns/<int:pk>/test-send/", campaigns.campaign_test_send, name="campaigns_test_send"),
    path("campaigns/<int:pk>/schedule/", campaigns.campaign_schedule, name="campaigns_schedule"),
    path("campaigns/<int:pk>/send-now/", campaigns.campaign_send_now, name="campaigns_send_now"),
    path("campaigns/<int:pk>/cancel/", campaigns.campaign_cancel, name="campaigns_cancel"),

    path("issues/", newsletters.issue_list, name="issues_list"),
    path("issues/<int:pk>/", newsletters.issue_detail, name="issues_change"),
    path("recommendations/", newsletters.recommendation_list, name="recommendations_list"),

    path("templates/", template_views.template_list, name="templates_list"),
    path("templates/start/<str:kind>/", template_views.template_from_builtin, name="templates_from_builtin"),
    path("templates/add/", template_views.template_form, name="templates_add"),
    path("templates/<int:pk>/", template_views.template_form, name="templates_change"),
    path("templates/<int:pk>/preview/", template_views.template_preview, name="templates_preview"),

    path("settings/", settings_views.siteconfig_form, name="siteconfig"),
    path("settings/reset-wording/", settings_views.reset_wording, name="siteconfig_reset_wording"),
]
