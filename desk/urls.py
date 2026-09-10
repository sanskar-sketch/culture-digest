from django.urls import path
from django.views.generic import RedirectView

from .views import access, auth, dashboard, deletion, insights, interests
from .views import listings, newsletters, outreach, readers, send
from .views import settings as settings_views
from .views import templates as template_views

app_name = "desk"

urlpatterns = [
    path("login/", auth.DeskLoginView.as_view(), name="login"),
    path("logout/", auth.DeskLogoutView.as_view(), name="logout"),
    path("password/", auth.DeskPasswordChangeView.as_view(), name="password_change"),
    path("password/done/", auth.DeskPasswordChangeDoneView.as_view(), name="password_change_done"),

    path("", dashboard.dashboard_view, name="dashboard"),

    # Events. The URL *names* keep their old "listings" prefix so nothing
    # that links to them has to change; only the path and the wording do.
    path("events/", listings.listing_list, name="listings_list"),
    path("events/review/", listings.listing_review, name="listings_review"),
    path("events/add/", listings.listing_form, name="listings_add"),
    path("events/<int:pk>/", listings.listing_form, name="listings_change"),
    path("events/<int:pk>/who/", listings.listing_suggest_audience, name="listings_who"),
    path("listings/", RedirectView.as_view(pattern_name="desk:listings_list", query_string=True)),

    path("interests/", interests.interest_list, name="interests_list"),
    path("interests/add/", interests.interest_form, name="interests_add"),
    path("interests/<int:pk>/", interests.interest_form, name="interests_change"),

    path("readers/", readers.reader_list, name="readers_list"),
    path("readers/<int:pk>/", readers.reader_form, name="readers_change"),
    path("readers/<int:pk>/tags/", outreach.reader_tags, name="reader_tags"),

    path("send/", send.send, name="send"),
    path("insights/", insights.insights, name="insights"),



    path("issues/", newsletters.issue_list, name="issues_list"),
    path("issues/<int:pk>/", newsletters.issue_detail, name="issues_change"),
    path("recommendations/", newsletters.recommendation_list, name="recommendations_list"),

    path("email-designs/", template_views.template_list, name="templates_list"),
    path("email-designs/start/<str:kind>/", template_views.template_from_builtin, name="templates_from_builtin"),
    path("email-designs/add/", template_views.template_form, name="templates_add"),
    path("email-designs/<int:pk>/", template_views.template_form, name="templates_change"),
    path("email-designs/<int:pk>/preview/", template_views.template_preview, name="templates_preview"),

    path("users/", access.user_list, name="users_list"),
    path("users/add/", access.user_form, name="users_add"),
    path("users/<int:pk>/", access.user_form, name="users_change"),
    path("users/<int:pk>/password/", access.user_password, name="users_password"),

    path("groups/", access.group_list, name="groups_list"),
    path("groups/add/", access.group_form, name="groups_add"),
    path("groups/<int:pk>/", access.group_form, name="groups_change"),

    # One delete for every section - see desk.views.deletion for why it
    # always shows what else goes first. The bulk form posts to the second.
    path("<str:kind>/<int:pk>/delete/", deletion.delete, name="delete"),
    path("<str:kind>/delete/", deletion.delete_selected, name="delete_selected"),

    path("settings/", settings_views.siteconfig_form, name="siteconfig"),
    path("settings/reset-wording/", settings_views.reset_wording, name="siteconfig_reset_wording"),
]
