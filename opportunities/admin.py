from django.contrib import admin

from .models import Opportunity, Tag


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "category",
        "status",
        "price_tier",
        "location_area",
        "mainstream_to_unusual",
        "intimate_to_large_scale",
        "critic_rating",
        "start_date",
        "end_date",
    )
    list_filter = (
        "status",
        "category",
        "price_tier",
        "is_online",
        "mainstream_to_unusual",
        "intimate_to_large_scale",
    )
    search_fields = ("title", "description", "location_area", "location_name", "tags__name")
    filter_horizontal = ("tags",)
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ()
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "start_date"
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
                "fields": (
                    "critic_rating",
                    "critic_rating_source",
                    "mainstream_to_unusual",
                    "intimate_to_large_scale",
                )
            },
        ),
        ("Metadata", {"fields": ("created_by", "created_at", "updated_at")}),
    )

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
