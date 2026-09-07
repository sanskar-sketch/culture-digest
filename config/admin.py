"""Custom admin site: The Ether branding plus an editorial dashboard.

The admin is the editorial team's main work surface for the MVP (curation
and tagging are deliberately manual), so the index does more than list
models - it surfaces catalogue health, coverage gaps and reader feedback.
"""

from datetime import timedelta

from django.contrib.admin import AdminSite
from django.db.models import Count, Q
from django.utils import timezone


class DigestAdminSite(AdminSite):
    site_header = "The Ether"
    site_title = "The Ether"
    index_title = "Editorial desk"
    # Named distinctly so it can extend Django's own admin/index.html
    # without the template loader resolving back to itself.
    index_template = "admin/digest_index.html"

    def index(self, request, extra_context=None):
        extra_context = {
            **(extra_context or {}),
            "digest_stats": self.dashboard_stats(),
            "digest_config": self.configuration(),
        }
        return super().index(request, extra_context=extra_context)

    def configuration(self):
        """What's wired up, for the dashboard.

        Reports presence and non-secret values only - never a key itself.
        Each row says what the consequence is when it's missing, since
        every integration here degrades quietly rather than erroring.
        """
        from django.conf import settings
        from django.db import connection

        engine = connection.settings_dict.get("ENGINE", "")
        host = connection.settings_dict.get("HOST") or ""
        on_postgres = "postgresql" in engine
        if on_postgres and "supabase" in host:
            db_detail = "Supabase Postgres"
        elif on_postgres:
            db_detail = f"Postgres ({host or 'unknown host'})"
        else:
            db_detail = "SQLite - data is lost on every deploy"

        return [
            {
                "name": "Database",
                "ok": on_postgres,
                "detail": db_detail,
            },
            {
                "name": "Email sending",
                "ok": bool(settings.SENDGRID_API_KEY),
                "detail": (
                    f"SendGrid key set · from {settings.EMAIL_FROM}"
                    if settings.SENDGRID_API_KEY
                    else "No SENDGRID_API_KEY - sends fall back to dry run"
                ),
            },
            {
                "name": "Link base URL",
                "ok": settings.SITE_BASE_URL.startswith("https://"),
                "detail": (
                    f"{settings.SITE_BASE_URL} - used for feedback and "
                    "unsubscribe links in emails"
                ),
            },
            {
                "name": "AI assistance",
                "ok": bool(settings.OPENAI_API_KEY),
                "detail": (
                    f"Key set · model {settings.OPENAI_MODEL}"
                    if settings.OPENAI_API_KEY
                    else "No OPENAI_API_KEY - rationales use the template"
                ),
            },
            {
                "name": "Debug mode",
                "ok": not settings.DEBUG,
                "detail": "Off (correct for production)" if not settings.DEBUG
                          else "ON - leaks tracebacks, must be off in production",
            },
            {
                "name": "Secret key",
                "ok": not settings.SECRET_KEY.startswith("django-insecure-"),
                "detail": "Set from the environment"
                          if not settings.SECRET_KEY.startswith("django-insecure-")
                          else "Using the insecure development default",
            },
        ]

    def dashboard_stats(self):
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
            live=Count(
                "id", filter=Q(status=Opportunity.Status.PUBLISHED) & live_window
            ),
        )

        feedback = Recommendation.objects.aggregate(
            total=Count("id"),
            more_like_this=Count(
                "id", filter=Q(feedback=Recommendation.Feedback.MORE_LIKE_THIS)
            ),
            not_for_me=Count("id", filter=Q(feedback=Recommendation.Feedback.NOT_FOR_ME)),
            saved=Count("id", filter=Q(feedback=Recommendation.Feedback.SAVE)),
            booked=Count("id", filter=Q(feedback=Recommendation.Feedback.BOOKED)),
        )
        responded = (
            feedback["more_like_this"]
            + feedback["not_for_me"]
            + feedback["saved"]
            + feedback["booked"]
        )
        feedback["responded"] = responded
        feedback["response_rate"] = (
            round(responded / feedback["total"] * 100) if feedback["total"] else 0
        )

        # Coverage per category, including the empty ones - a gap in the
        # catalogue is the most useful thing an editor can be shown.
        counts = dict(
            Opportunity.objects.filter(status=Opportunity.Status.PUBLISHED)
            .filter(live_window)
            .values_list("category")
            .annotate(n=Count("id"))
        )
        coverage = [
            {"label": label, "value": value, "count": counts.get(value, 0)}
            for value, label in Category.choices
            if value != Category.OTHER
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
