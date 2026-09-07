from django.contrib import admin, messages
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from recommendations import ai
from recommendations.models import NewsletterIssue, Recommendation

from .models import Reader

# Fields that make up a reader's taste profile. Everything is optional, so
# how much of it is filled in is itself useful editorial signal.
PROFILE_FIELDS = (
    "name", "age", "location", "travel_destinations", "travel_radius",
    "budget", "availability", "mainstream_preference", "scale_preference",
    "loved_examples", "disliked_examples",
)


class NewsletterIssueInline(admin.TabularInline):
    model = NewsletterIssue
    extra = 0
    can_delete = False
    fields = ("issue_link", "created_at", "sent_at", "picks")
    readonly_fields = fields
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Issue")
    def issue_link(self, obj):
        url = reverse("admin:recommendations_newsletterissue_change", args=[obj.pk])
        return format_html('<a href="{}">Issue #{}</a>', url, obj.pk)

    @admin.display(description="What they were sent")
    def picks(self, obj):
        rows = [
            (rec.opportunity.title, rec.get_feedback_display())
            for rec in obj.recommendations.select_related("opportunity")
        ]
        if not rows:
            return "—"
        return format_html_join(
            mark_safe("<br>"), "{} — <em>{}</em>", rows
        )


@admin.register(Reader)
class ReaderAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "name",
        "age",
        "location",
        "categories_summary",
        "budget",
        "travel_radius",
        "engagement",
        "profile_completeness",
        "is_active",
        "created_at",
    )
    list_display_links = ("email",)
    list_filter = (
        "is_active",
        "open_to_surprise",
        "budget",
        "travel_radius",
        "mainstream_preference",
        "scale_preference",
        "created_at",
        "location",
    )
    search_fields = ("email", "name", "location", "travel_destinations",
                     "loved_examples", "disliked_examples")
    filter_horizontal = ("interest_tags", "ai_inferred_tags", "ai_avoid_tags")
    readonly_fields = ("unsubscribe_token", "created_at", "updated_at",
                       "profile_completeness", "engagement", "feedback_breakdown",
                       "ai_profile_updated_at")
    inlines = [NewsletterIssueInline]
    list_per_page = 50
    actions = ("interpret_taste",)

    fieldsets = (
        ("Who they are", {"fields": ("email", "name", "age", "is_active")}),
        ("Where they are", {
            "fields": ("location", "travel_radius", "travel_destinations"),
        }),
        ("Taste", {
            "fields": ("interest_categories", "interest_tags",
                       "mainstream_preference", "scale_preference",
                       "open_to_surprise", "loved_examples", "disliked_examples"),
        }),
        ("Practical", {"fields": ("budget", "availability")}),
        ("What AI read into their free text", {
            "description": "Inferred, not stated by the reader - weighted below their own "
                           "picks when matching. Run the 'Interpret taste' action to refresh.",
            "fields": ("ai_taste_summary", "ai_inferred_tags", "ai_avoid_tags",
                       "ai_profile_updated_at"),
        }),
        ("Engagement", {"fields": ("profile_completeness", "engagement", "feedback_breakdown")}),
        ("Metadata", {
            "classes": ("collapse",),
            "fields": ("unsubscribe_token", "created_at", "updated_at"),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _recs=Count("issues__recommendations", distinct=True),
            _replied=Count(
                "issues__recommendations",
                filter=~Q(issues__recommendations__feedback=Recommendation.Feedback.NONE),
                distinct=True,
            ),
        ).prefetch_related("interest_tags")

    @admin.action(description="Interpret taste from free text with AI")
    def interpret_taste(self, request, queryset):
        if not ai.is_enabled():
            self.message_user(
                request,
                "AI is not configured - set OPENAI_API_KEY to enable this.",
                messages.WARNING,
            )
            return
        done = sum(1 for reader in queryset if ai.apply_interpretation(reader))
        failed = queryset.count() - done
        self.message_user(
            request,
            f"Interpreted {done} reader profile{'' if done == 1 else 's'}."
            + (f" {failed} could not be interpreted - see the logs." if failed else ""),
            messages.SUCCESS if done else messages.WARNING,
        )

    @admin.display(description="Follows")
    def categories_summary(self, obj):
        if not obj.interest_categories:
            return format_html('<span style="color:#8e8e93">anything</span>')
        return ", ".join(obj.interest_categories)

    @admin.display(description="Sent / replied", ordering="_recs")
    def engagement(self, obj):
        sent = getattr(obj, "_recs", None)
        if sent is None:
            sent = Recommendation.objects.filter(issue__reader=obj).count()
            replied = Recommendation.objects.filter(issue__reader=obj).exclude(
                feedback=Recommendation.Feedback.NONE).count()
        else:
            replied = getattr(obj, "_replied", 0)
        if not sent:
            return "—"
        return format_html("{} sent · <strong>{}</strong> replied", sent, replied)

    @admin.display(description="Profile")
    def profile_completeness(self, obj):
        filled = 0
        for field in PROFILE_FIELDS:
            value = getattr(obj, field)
            if value not in (None, "", [], {}):
                filled += 1
        total = len(PROFILE_FIELDS) + 2  # + categories + tags
        if obj.interest_categories:
            filled += 1
        if obj.pk and obj.interest_tags.exists():
            filled += 1
        pct = round(filled / total * 100)
        colour = "#34c759" if pct >= 70 else "#ff9500" if pct >= 35 else "#8e8e93"
        return format_html(
            '<div title="{} of {} answered" style="display:flex;align-items:center;gap:8px">'
            '<span style="flex:0 0 60px;height:6px;border-radius:999px;background:rgba(125,125,130,.2);'
            'overflow:hidden;display:inline-block">'
            '<span style="display:block;height:100%;width:{}%;background:{}"></span></span>'
            '<span style="font-variant-numeric:tabular-nums">{}%</span></div>',
            filled, total, pct, colour, pct,
        )

    @admin.display(description="Feedback so far")
    def feedback_breakdown(self, obj):
        if not obj.pk:
            return "—"
        counts = (
            Recommendation.objects.filter(issue__reader=obj)
            .values("feedback").annotate(n=Count("id"))
        )
        labels = dict(Recommendation.Feedback.choices)
        rows = [(labels.get(c["feedback"], c["feedback"]), c["n"]) for c in counts if c["n"]]
        if not rows:
            return format_html('<span style="color:#8e8e93">Nothing yet</span>')
        return format_html_join(mark_safe("<br>"), "{}: <strong>{}</strong>", rows)
