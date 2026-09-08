"""Editorial dashboard data: what's wired up, and today's numbers.

Shared by the desk (the editors' real working surface) and by the old
Django admin, which stays reachable at /admin/ for Users and Groups.
Registry-only where possible, so it costs nothing to compute on every
request; the heavier query-based stats are cached separately by the caller.
"""

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

# The desk's navigation, arranged by what an editor is trying to do rather
# than by which Django app a model happens to live in.
SECTIONS = [
    {
        "key": "catalogue",
        "title": "The catalogue",
        "blurb": "Everything the newsletter can draw on. This is where the work is.",
        "rows": [
            {"key": "listings", "name": "Listings",
             "blurb": "Shows, exhibitions, meals, talks, walks - everything the "
                      "newsletter can recommend. A listing stays a draft, invisible "
                      "to readers, until you publish it."},
            {"key": "interests", "name": "Interests",
             "blurb": "The interests readers pick from when they sign up, and that "
                      "you tag listings with. Where the two overlap is how a "
                      "listing finds its reader."},
        ],
    },
    {
        "key": "readers",
        "title": "Readers",
        "blurb": "Who is subscribed, and what they told us about their taste.",
        "rows": [
            {"key": "readers", "name": "Readers",
             "blurb": "Everyone subscribed: what they told us about their taste, "
                      "what they have been sent, and how they responded."},
        ],
    },
    {
        "key": "goes-out",
        "title": "What goes out",
        "blurb": "The newsletters themselves, and how they read.",
        "rows": [
            {"key": "campaigns", "name": "Campaigns",
             "blurb": "One-off emails on any subject - to everyone or a slice of "
                      "readers, now or at a set time."},
            {"key": "issues", "name": "Newsletter issues",
             "blurb": "One row per newsletter sent to one reader, with the picks "
                      "it contained."},
            {"key": "recommendations", "name": "Recommendations",
             "blurb": "Every individual pick ever made, with the reader's verdict "
                      "on it. Where you see what lands and what doesn't."},
            {"key": "templates", "name": "Email templates",
             "blurb": "The welcome, newsletter and campaign emails as editable "
                      "templates, with preview. Nothing here means the built-in "
                      "versions are used."},
        ],
    },
    {
        "key": "settings",
        "title": "Settings",
        "blurb": "How the whole thing behaves - branding, schedule, matching, AI.",
        "rows": [
            {"key": "siteconfig", "name": "Site configuration",
             "blurb": "One page, tabbed: branding, sending, schedule, matching "
                      "weights, feedback learning and AI."},
        ],
    },
    {
        "key": "access",
        "title": "Access",
        "blurb": "Who can get in here.",
        "collapsed": True,
        "rows": [
            {"key": "users", "name": "Users", "blurb": "Editor accounts.",
             "external_admin_url_name": "admin:auth_user_changelist"},
            {"key": "groups", "name": "Groups",
             "blurb": "Permission groups, if you want more than one kind of editor.",
             "external_admin_url_name": "admin:auth_group_changelist"},
        ],
    },
]


def configuration() -> list[dict]:
    """What's wired up. Reports presence and non-secret values only - never a
    key itself. Each row says what breaks when it's missing, since every
    integration here degrades quietly rather than erroring."""
    from django.conf import settings
    from django.db import connection

    from campaigns import scheduler

    engine = connection.settings_dict.get("ENGINE", "")
    host = connection.settings_dict.get("HOST") or ""
    on_postgres = "postgresql" in engine
    if on_postgres and "supabase" in host:
        db_detail = "Supabase Postgres"
    elif on_postgres:
        db_detail = f"Postgres ({host or 'unknown host'})"
    else:
        db_detail = "SQLite - data is lost on every deploy"

    if not settings.SCHEDULER_TOKEN:
        scheduler_row = {
            "name": "Scheduler", "ok": False,
            "detail": "No SCHEDULER_TOKEN - scheduled campaigns and the newsletter "
                      "cadence won't run until it is set and something pings "
                      "/tasks/run-scheduled/",
        }
    else:
        last = scheduler.last_run()
        if not last:
            scheduler_row = {"name": "Scheduler", "ok": True,
                             "detail": "Token set · waiting for the first ping at "
                                       "/tasks/run-scheduled/"}
        else:
            from django.utils.timesince import timesince

            scheduler_row = {"name": "Scheduler", "ok": True,
                             "detail": f"Last pass {timesince(last['at'])} ago · "
                                       + " · ".join(last["lines"])}

    return [
        {"name": "Database", "ok": on_postgres, "detail": db_detail},
        {"name": "Email sending", "ok": bool(settings.SENDGRID_API_KEY),
         "detail": (f"SendGrid key set · from {settings.EMAIL_FROM}"
                    if settings.SENDGRID_API_KEY
                    else "No SENDGRID_API_KEY - sends fall back to dry run")},
        {"name": "Link base URL", "ok": settings.SITE_BASE_URL.startswith("https://"),
         "detail": f"{settings.SITE_BASE_URL} - used for feedback and unsubscribe "
                   "links in emails"},
        {"name": "AI assistance", "ok": bool(settings.OPENAI_API_KEY),
         "detail": (f"Key set · model {settings.OPENAI_MODEL}"
                    if settings.OPENAI_API_KEY
                    else "No OPENAI_API_KEY - rationales use the template")},
        scheduler_row,
        {"name": "Debug mode", "ok": not settings.DEBUG,
         "detail": "Off (correct for production)" if not settings.DEBUG
                   else "ON - leaks tracebacks, must be off in production"},
        {"name": "Secret key", "ok": not settings.SECRET_KEY.startswith("django-insecure-"),
         "detail": "Set from the environment"
                   if not settings.SECRET_KEY.startswith("django-insecure-")
                   else "Using the insecure development default"},
    ]


def stats(use_cache: bool = True) -> dict:
    from django.core.cache import cache

    if use_cache:
        cached = cache.get("digest_dashboard_stats")
        if cached is not None:
            return cached
    computed = _compute_stats()
    cache.set("digest_dashboard_stats", computed, 60)
    return computed


def _compute_stats() -> dict:
    from opportunities.models import Category, Opportunity, Tag
    from readers.models import Reader
    from recommendations.models import NewsletterIssue, Recommendation

    today = timezone.localdate()
    week_ago = timezone.now() - timedelta(days=7)
    live_window = Q(end_date__isnull=True) | Q(end_date__gte=today)

    readers = Reader.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
        new_this_week=Count("id", filter=Q(created_at__gte=week_ago)),
        open_to_surprise=Count("id", filter=Q(open_to_surprise=True)),
    )

    opportunities = Opportunity.objects.aggregate(
        total=Count("id"),
        published=Count("id", filter=Q(status=Opportunity.Status.PUBLISHED)),
        draft=Count("id", filter=Q(status=Opportunity.Status.DRAFT)),
        live=Count("id", filter=Q(status=Opportunity.Status.PUBLISHED) & live_window),
    )

    feedback = Recommendation.objects.aggregate(
        total=Count("id"),
        more_like_this=Count("id", filter=Q(feedback=Recommendation.Feedback.MORE_LIKE_THIS)),
        not_for_me=Count("id", filter=Q(feedback=Recommendation.Feedback.NOT_FOR_ME)),
        saved=Count("id", filter=Q(feedback=Recommendation.Feedback.SAVE)),
        booked=Count("id", filter=Q(feedback=Recommendation.Feedback.BOOKED)),
    )
    responded = (feedback["more_like_this"] + feedback["not_for_me"]
                 + feedback["saved"] + feedback["booked"])
    feedback["responded"] = responded
    feedback["response_rate"] = round(responded / feedback["total"] * 100) if feedback["total"] else 0

    counts = dict(
        Opportunity.objects.filter(status=Opportunity.Status.PUBLISHED)
        .filter(live_window).values_list("category").annotate(n=Count("id"))
    )
    coverage = [
        {"label": label, "value": value, "count": counts.get(value, 0)}
        for value, label in Category.choices if value != Category.OTHER
    ]
    coverage.sort(key=lambda row: row["count"])

    return {
        "readers": readers,
        "opportunities": opportunities,
        "feedback": feedback,
        "coverage": coverage,
        "empty_categories": [row for row in coverage if not row["count"]],
        "tags_total": Tag.objects.count(),
        "tags_unused": Tag.objects.filter(opportunities__isnull=True).count(),
        "issues_total": NewsletterIssue.objects.count(),
        "issues_unsent": NewsletterIssue.objects.filter(sent_at__isnull=True).count(),
    }
