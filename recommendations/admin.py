from django.contrib import admin

from .models import NewsletterIssue, Recommendation


class RecommendationInline(admin.TabularInline):
    model = Recommendation
    extra = 0
    fields = ("opportunity", "score", "rationale", "feedback", "feedback_at")
    readonly_fields = ("feedback_at",)


@admin.register(NewsletterIssue)
class NewsletterIssueAdmin(admin.ModelAdmin):
    list_display = ("reader", "created_at", "sent_at", "recommendation_count")
    list_filter = ("sent_at",)
    search_fields = ("reader__email", "reader__name")
    inlines = [RecommendationInline]

    def recommendation_count(self, obj):
        return obj.recommendations.count()


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = ("opportunity", "reader_email", "score", "feedback", "feedback_at", "created_at")
    list_filter = ("feedback", "created_at")
    search_fields = ("opportunity__title", "issue__reader__email")
    readonly_fields = ("feedback_token", "created_at")

    def reader_email(self, obj):
        return obj.issue.reader.email
