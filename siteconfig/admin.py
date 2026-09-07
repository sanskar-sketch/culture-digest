from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from .models import SiteConfig


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    """A singleton, so the changelist is skipped - clicking through from the
    admin index lands straight on the one editable record."""

    save_on_top = True
    readonly_fields = ("updated_at",)

    fieldsets = (
        ("Branding", {
            "description": "Shown on the public pages and in the newsletter.",
            "fields": ("site_name", "tagline", "hero_eyebrow", "hero_headline",
                       "hero_subhead"),
        }),
        ("Sending", {
            "description": "How many picks go out, to whom, and how often the same "
                           "thing may reappear.",
            "fields": ("recommendations_per_send", "min_recommendations",
                       "cooldown_days", "email_from", "subject_template",
                       "send_welcome_email", "welcome_subject"),
        }),
        ("Matching — what raises a score", {
            "description": "Higher numbers mean a stronger pull. These decide which "
                           "opportunities a reader actually sees.",
            "fields": ("weight_tag_overlap", "weight_category",
                       "weight_inferred_tag", "weight_critic_rating"),
        }),
        ("Matching — what lowers or excludes", {
            "fields": ("penalty_avoid_tag", "penalty_price_step", "max_price_distance",
                       "penalty_mainstream_gap", "penalty_scale_gap",
                       "penalty_out_of_area"),
        }),
        ("Learning from feedback", {
            "description": "How much each feedback click shifts future picks. This is "
                           "the loop that makes recommendations improve over time.",
            "fields": ("feedback_more_like_this", "feedback_saved", "feedback_booked",
                       "feedback_not_for_me", "dislike_drop_threshold"),
        }),
        ("AI assistance", {
            "description": "Every feature falls back to a deterministic template when "
                           "switched off or unavailable. API keys stay in the "
                           "environment, not here.",
            "fields": ("ai_enabled", "ai_write_rationales", "ai_interpret_readers",
                       "ai_classify_opportunities", "ai_model", "ai_timeout_seconds",
                       "ai_send_budget_seconds"),
        }),
        ("Metadata", {"classes": ("collapse",), "fields": ("updated_at",)}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        config = SiteConfig.load()
        return redirect(reverse("admin:siteconfig_siteconfig_change", args=[config.pk]))
