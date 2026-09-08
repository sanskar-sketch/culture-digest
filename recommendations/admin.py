from django.contrib import admin
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.html import format_html

from .models import NewsletterIssue, Recommendation

FEEDBACK_COLOURS = {
    Recommendation.Feedback.MORE_LIKE_THIS: "#5e5ce6",
    Recommendation.Feedback.BOOKED: "#34c759",
    Recommendation.Feedback.SAVE: "#0071e3",
    Recommendation.Feedback.NOT_FOR_ME: "#ff375f",
}


def feedback_badge(value, label):
    colour = FEEDBACK_COLOURS.get(value)
    if not colour:
        return format_html('<span style="color:#8e8e93">—</span>')
    return format_html(
        '<span class="cd-pill" style="background:{}1f;color:{}">{}</span>',
        colour, colour, label,
    )


class RecommendationInline(admin.TabularInline):
    model = Recommendation
    extra = 0
    fields = ("opportunity", "score", "rationale", "feedback", "feedback_at")
    readonly_fields = ("feedback_at",)
    autocomplete_fields = ("opportunity",)


class DeliveryFilter(admin.SimpleListFilter):
    title = "delivery"
    parameter_name = "delivery"

    def lookups(self, request, model_admin):
        return (("sent", "Sent"), ("unsent", "Not sent"))

    def queryset(self, request, queryset):
        if self.value() == "sent":
            return queryset.filter(sent_at__isnull=False)
        if self.value() == "unsent":
            return queryset.filter(sent_at__isnull=True)
        return queryset


@admin.register(NewsletterIssue)
class NewsletterIssueAdmin(admin.ModelAdmin):
    list_display = ("__str__", "reader_link", "recommendation_count", "replies",
                    "status", "created_at", "sent_at")
    list_display_links = ("__str__",)
    list_filter = (DeliveryFilter, "created_at")
    search_fields = ("reader__email", "reader__name", "recommendations__opportunity__title")
    inlines = [RecommendationInline]
    readonly_fields = ("created_at", "provider_message_id")
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("reader").annotate(
            _recs=Count("recommendations", distinct=True),
            _replied=Count(
                "recommendations",
                filter=~Q(recommendations__feedback=Recommendation.Feedback.NONE),
                distinct=True,
            ),
        )

    @admin.display(description="Reader")
    def reader_link(self, obj):
        url = reverse("admin:readers_reader_change", args=[obj.reader_id])
        return format_html('<a href="{}">{}</a>', url, obj.reader.email)

    @admin.display(description="Picks", ordering="_recs")
    def recommendation_count(self, obj):
        return obj._recs

    @admin.display(description="Replies", ordering="_replied")
    def replies(self, obj):
        return obj._replied or "—"

    @admin.display(description="Sent?", boolean=True)
    def status(self, obj):
        return obj.sent_at is not None


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = ("opportunity", "reader_link", "score", "feedback_display",
                    "feedback_at", "created_at")
    list_display_links = ("opportunity",)
    list_filter = ("feedback", "created_at", "opportunity__category")
    search_fields = ("opportunity__title", "issue__reader__email", "rationale")
    readonly_fields = ("feedback_token", "created_at", "rationale", "score")
    autocomplete_fields = ("opportunity",)
    date_hierarchy = "created_at"
    list_per_page = 50

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("opportunity", "issue__reader")

    @admin.display(description="Reader", ordering="issue__reader__email")
    def reader_link(self, obj):
        url = reverse("admin:readers_reader_change", args=[obj.issue.reader_id])
        return format_html('<a href="{}">{}</a>', url, obj.issue.reader.email)

    @admin.display(description="Feedback", ordering="feedback")
    def feedback_display(self, obj):
        return feedback_badge(obj.feedback, obj.get_feedback_display())
