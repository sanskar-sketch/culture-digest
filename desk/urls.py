from django.urls import path

from .views import access, auth, campaigns, dashboard, deletion, insights, interests
from .views import listings, newsletters, outreach, readers, saved_templates
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
    path("readers/<int:pk>/tags/", outreach.reader_tags, name="reader_tags"),

    path("insights/", insights.insights, name="insights"),

    path("campaigns/", campaigns.campaign_list, name="campaigns_list"),
    path("campaigns/add/", campaigns.campaign_form, name="campaigns_add"),
    path("campaigns/<int:pk>/", campaigns.campaign_form, name="campaigns_change"),
    path("campaigns/<int:pk>/preview/", campaigns.campaign_preview, name="campaigns_preview"),
    path("campaigns/<int:pk>/test-send/", campaigns.campaign_test_send, name="campaigns_test_send"),
    path("campaigns/<int:pk>/schedule/", campaigns.campaign_schedule, name="campaigns_schedule"),
    path("campaigns/<int:pk>/send-now/", campaigns.campaign_send_now, name="campaigns_send_now"),
    path("campaigns/<int:pk>/cancel/", campaigns.campaign_cancel, name="campaigns_cancel"),
    path("campaigns/<int:pk>/suggest-audience/", campaigns.campaign_suggest_audience,
         name="campaigns_suggest_audience"),
    path("campaigns/from-idea/", campaigns.campaign_from_idea, name="campaigns_from_idea"),
    path("campaigns/from-template/<int:pk>/", campaigns.campaign_from_template,
         name="campaigns_from_template"),

    # Saved sends. The email designs keep their URL *names* (templates_*) so
    # nothing that links to them changes; only their path moves.
    path("templates/", saved_templates.template_list, name="saved_templates_list"),
    path("templates/add/", saved_templates.template_form, name="saved_templates_add"),
    path("templates/<int:pk>/", saved_templates.template_form, name="saved_templates_change"),
    path("templates/<int:pk>/preview/", saved_templates.template_preview,
         name="saved_templates_preview"),
    path("templates/<int:pk>/run/", saved_templates.template_run, name="saved_templates_run"),
    path("templates/<int:pk>/suggest-audience/", saved_templates.template_suggest_audience,
         name="saved_templates_suggest"),
    path("templates/<int:pk>/start-campaign/", saved_templates.template_to_campaign,
         name="saved_templates_to_campaign"),

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
