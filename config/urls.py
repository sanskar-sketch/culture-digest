from django.urls import include, path, re_path
from django.views.generic import RedirectView

from campaigns.views import run_scheduled

from .health import healthz

# Django's admin is gone entirely - the desk replaced every screen it had.
# These redirects outlive it so that a bookmark, or a link in an old email,
# still lands on the working page rather than a 404.
RETIRED_ADMIN_PAGES = {
    "opportunities/opportunity": "desk:listings_list",
    "opportunities/tag": "desk:interests_list",
    "readers/reader": "desk:readers_list",
    "campaigns/campaign": "desk:readers_list",
    "recommendations/newsletterissue": "desk:issues_list",
    "recommendations/recommendation": "desk:recommendations_list",
    "siteconfig/siteconfig": "desk:siteconfig",
    "siteconfig/emailtemplate": "desk:templates_list",
    "auth/user": "desk:users_list",
    "auth/group": "desk:groups_list",
}

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("tasks/run-scheduled/", run_scheduled, name="run-scheduled"),
    path("desk/", include("desk.urls")),
    path("admin/", RedirectView.as_view(pattern_name="desk:dashboard")),
    *[
        re_path(rf"^admin/{prefix}/", RedirectView.as_view(pattern_name=target))
        for prefix, target in RETIRED_ADMIN_PAGES.items()
    ],
    path("r/", include("recommendations.urls")),
    path("", include("readers.urls")),
]
