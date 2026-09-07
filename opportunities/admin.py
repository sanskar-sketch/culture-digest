from django.contrib import admin, messages
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html

from .models import Opportunity, Tag


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "opportunity_count", "reader_count", "slug")
    list_filter = ("category",)
    list_editable = ("category",)
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    list_per_page = 100

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _opportunities=Count("opportunities", distinct=True),
            _readers=Count("interested_readers", distinct=True),
        )

    @admin.display(description="Opportunities", ordering="_opportunities")
    def opportunity_count(self, obj):
        count = obj._opportunities
        if not count:
            # An interest nothing is tagged with can never be matched on.
            return format_html('<span style="color:#ff375f">none yet</span>')
        return count

    @admin.display(description="Readers interested", ordering="_readers")
    def reader_count(self, obj):
        return obj._readers or "—"


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "category",
        "state",
        "price_tier",
        "location_area",
        "taste_profile",
        "critic_rating",
        "tag_list",
        "times_recommended",
        "end_date",
    )
    list_display_links = ("title",)
    list_filter = (
        "status",
        "category",
        "price_tier",
        "is_online",
        "mainstream_to_unusual",
        "intimate_to_large_scale",
        "tags",
        "location_area",
        "start_date",
    )
    search_fields = ("title", "description", "editorial_note", "location_area",
                     "location_name", "tags__name")
    filter_horizontal = ("tags",)
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at", "performance")
    date_hierarchy = "start_date"
    list_per_page = 50
    actions = ("publish", "archive", "back_to_draft")
    save_on_top = True

    fieldsets = (
        (None, {"fields": ("title", "slug", "category", "status", "tags")}),
        ("Content", {"fields": ("description", "editorial_note")}),
        (
            "Practical attributes",
            {
                "fields": (
                    "price_tier",
                    "price_display",
                    "location_name",
                    "location_area",
                    "is_online",
                    "booking_url",
                    "start_date",
                    "end_date",
                )
            },
        ),
        (
            "Taste attributes",
            {
                "description": "These drive matching against a reader's stated taste.",
                "fields": (
                    "critic_rating",
                    "critic_rating_source",
                    "mainstream_to_unusual",
                    "intimate_to_large_scale",
                ),
            },
        ),
        ("Performance", {"fields": ("performance",)}),
        ("Metadata", {"classes": ("collapse",),
                      "fields": ("created_by", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _recs=Count("recommendations", distinct=True),
        ).prefetch_related("tags")

    @admin.display(description="Status")
    def state(self, obj):
        """Published-but-finished is invisible to readers, but the plain
        status field still says 'published' - so surface it separately."""
        today = timezone.localdate()
        if obj.status == Opportunity.Status.PUBLISHED:
            if obj.end_date and obj.end_date < today:
                return format_html('<span class="cd-pill cd-pill-ended">Ended</span>')
            return format_html('<span class="cd-pill cd-pill-live">Live</span>')
        if obj.status == Opportunity.Status.DRAFT:
            return format_html('<span class="cd-pill cd-pill-draft">Draft</span>')
        return format_html('<span class="cd-pill cd-pill-archived">Archived</span>')

    @admin.display(description="Taste")
    def taste_profile(self, obj):
        return format_html(
            '<span title="mainstream→unusual / intimate→large-scale"'
            ' style="font-variant-numeric:tabular-nums">{}·{}</span>',
            obj.mainstream_to_unusual, obj.intimate_to_large_scale,
        )

    @admin.display(description="Tags")
    def tag_list(self, obj):
        tags = list(obj.tags.all())
        if not tags:
            return format_html('<span style="color:#ff375f">untagged</span>')
        shown = ", ".join(t.name for t in tags[:3])
        if len(tags) > 3:
            shown += f" +{len(tags) - 3}"
        return shown

    @admin.display(description="Recommended", ordering="_recs")
    def times_recommended(self, obj):
        return obj._recs or "—"

    @admin.display(description="How it has performed")
    def performance(self, obj):
        if not obj.pk:
            return "—"
        from recommendations.models import Recommendation

        stats = obj.recommendations.aggregate(
            sent=Count("id"),
            booked=Count("id", filter=Q(feedback=Recommendation.Feedback.BOOKED)),
            saved=Count("id", filter=Q(feedback=Recommendation.Feedback.SAVE)),
            more=Count("id", filter=Q(feedback=Recommendation.Feedback.MORE_LIKE_THIS)),
            nope=Count("id", filter=Q(feedback=Recommendation.Feedback.NOT_FOR_ME)),
        )
        if not stats["sent"]:
            return format_html('<span style="color:#8e8e93">Not recommended to anyone yet</span>')
        return format_html(
            "Recommended <strong>{}</strong> time{} · {} booked · {} saved · "
            "{} wanted more like it · {} said not for them",
            stats["sent"], "" if stats["sent"] == 1 else "s",
            stats["booked"], stats["saved"], stats["more"], stats["nope"],
        )

    def _set_status(self, request, queryset, status, label):
        updated = queryset.update(status=status)
        self.message_user(
            request,
            f"{updated} opportunit{'y' if updated == 1 else 'ies'} marked {label}.",
            messages.SUCCESS,
        )

    @admin.action(description="Publish selected opportunities")
    def publish(self, request, queryset):
        self._set_status(request, queryset, Opportunity.Status.PUBLISHED, "published")

    @admin.action(description="Archive selected opportunities")
    def archive(self, request, queryset):
        self._set_status(request, queryset, Opportunity.Status.ARCHIVED, "archived")

    @admin.action(description="Move selected back to draft")
    def back_to_draft(self, request, queryset):
        self._set_status(request, queryset, Opportunity.Status.DRAFT, "draft")

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
