from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView

from campaigns.views import run_scheduled

from .health import healthz

# Screens the desk replaced. That is now all of them, Users and Groups
# included, so nothing in the old admin is reachable any more. It stays
# mounted only because reverse("admin:…") still resolves in a few places;
# every path below lands on the desk instead. Anything not listed still
# renders with the "this has moved" banner from
# templates/admin/base_site.html as the fallback.
RETIRED_ADMIN_PAGES = {
    "opportunities/opportunity": "desk:listings_list",
    "opportunities/tag": "desk:interests_list",
    "readers/reader": "desk:readers_list",
    "campaigns/campaign": "desk:campaigns_list",
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
    # These have to precede admin.site.urls to win over it.
    path("admin/", RedirectView.as_view(pattern_name="desk:dashboard")),
    *[
        re_path(rf"^admin/{prefix}/", RedirectView.as_view(pattern_name=target))
        for prefix, target in RETIRED_ADMIN_PAGES.items()
    ],
    path("admin/", admin.site.urls),
    path("r/", include("recommendations.urls")),
    path("", include("readers.urls")),
]
