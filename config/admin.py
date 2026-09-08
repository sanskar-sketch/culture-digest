"""Custom admin site: The Ether branding plus an editorial dashboard.

The admin is the editorial team's main work surface for the MVP (curation
and tagging are deliberately manual), so the index does more than list
models - it surfaces catalogue health, coverage gaps and reader feedback.
"""

from datetime import timedelta

from django.contrib.admin import AdminSite
from django.db.models import Count, Q
from django.utils import timezone


# The admin index, arranged by what an editor is trying to do rather than
# by which Django app a model happens to live in. "SITECONFIG" and
# "RECOMMENDATIONS" are facts about the codebase, not about the job.
SECTIONS = [
    {
        "title": "The catalogue",
        "blurb": "Everything the newsletter can draw on. This is where the work is.",
        "models": [
            ("opportunities.opportunity", "Shows, exhibitions, meals, talks, walks - "
                                          "everything the newsletter can recommend. A "
                                          "listing stays a draft, invisible to readers, "
                                          "until you publish it."),
            ("opportunities.tag", "The interests readers pick from when they sign up, "
                                  "and that you tag listings with. Where the two "
                                  "overlap is how a listing finds its reader."),
        ],
    },
    {
        "title": "Readers",
        "blurb": "Who is subscribed, and what they told us about their taste.",
        "models": [
            ("readers.reader", "Everyone subscribed: what they told us about their "
                               "taste, what they have been sent, and how they responded."),
        ],
    },
    {
        "title": "What goes out",
        "blurb": "The newsletters themselves, and how they read.",
        "models": [
            ("campaigns.campaign", "One-off emails on any subject - to everyone or a "
                                   "slice of readers, now or at a set time."),
            ("recommendations.newsletterissue", "One row per newsletter sent to one "
                                                "reader, with the picks it contained."),
            ("recommendations.recommendation", "Every individual pick ever made, with "
                                               "the reader's verdict on it. Where you see "
                                               "what lands and what doesn't."),
            ("siteconfig.emailtemplate", "The welcome, newsletter and campaign emails as "
                                         "editable templates, with preview. Nothing here "
                                         "means the built-in versions are used."),
        ],
    },
    {
        "title": "Settings",
        "blurb": "How the whole thing behaves — branding, schedule, matching, AI.",
        "models": [
            ("siteconfig.siteconfig", "One page, tabbed: branding, sending, schedule, "
                                      "matching weights, feedback learning and AI."),
        ],
    },
    {
        "title": "Access",
        "blurb": "Who can get in here.",
        "collapsed": True,
        "models": [
            ("auth.user", "Editor accounts."),
            ("auth.group", "Permission groups, if you want more than one kind of editor."),
        ],
    },
]


class DigestAdminSite(AdminSite):
    site_header = "The Ether"
    site_title = "The Ether"
    index_title = "Editorial desk"
    # Named distinctly so it can extend Django's own admin/index.html
    # without the template loader resolving back to itself.
    index_template = "admin/digest_index.html"

    def each_context(self, request):
        """The task-grouped navigation on every page, not just the index.

        Registry-only - no database queries - so it costs nothing to put in
        the sidebar of every request.
        """
        context = super().each_context(request)
        if getattr(request, "user", None) and request.user.is_active and request.user.is_staff:
            context["digest_sections"] = self.sections(request)
        return context

    def index(self, request, extra_context=None):
        extra_context = {
            **(extra_context or {}),
            "digest_stats": self.dashboard_stats(),
            "digest_config": self.configuration(),
            "digest_sections": self.sections(request),
        }
        return super().index(request, extra_context=extra_context)

    def sections(self, request):
        """The registered models, grouped by task and described.

        Anything registered but not placed in SECTIONS still appears, under
        "Everything else" - a new model should never become invisible just
        because nobody added it here.
        """
        available = {}
        for app in super().get_app_list(request):
            for model in app["models"]:
                key = f"{app['app_label']}.{model['object_name']}".lower()
                available[key] = model

        path = getattr(request, "path", "") or ""
        sections, placed = [], set()
        for section in SECTIONS:
            rows = []
            for key, blurb in section["models"]:
                model = available.get(key)
                if not model:
                    continue  # not registered, or this user can't see it
                admin_url = model.get("admin_url") or ""
                rows.append({
                    **model,
                    "blurb": blurb,
                    # Highlights the sidebar entry for the area you're in,
                    # including its add/change pages.
                    "active": bool(admin_url) and path.startswith(admin_url),
                })
                placed.add(key)
            if rows:
                sections.append({**section, "rows": rows})

        leftover = [
            {**model, "blurb": ""}
            for key, model in available.items() if key not in placed
        ]
        if leftover:
            sections.append({
                "title": "Everything else",
                "blurb": "Registered but not yet placed in a section.",
                "rows": leftover,
            })
        return sections

    def configuration(self):
        """What's wired up, for the dashboard. Shared with the desk - see
        config.dashboard for the implementation."""
        from . import dashboard

        return dashboard.configuration()

    def dashboard_stats(self):
        from . import dashboard

        return dashboard.stats()
