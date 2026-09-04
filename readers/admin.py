from django.contrib import admin

from .models import Reader


@admin.register(Reader)
class ReaderAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "name",
        "age",
        "location",
        "budget",
        "travel_radius",
        "open_to_surprise",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "open_to_surprise", "budget", "travel_radius", "location")
    search_fields = ("email", "name", "location", "travel_destinations")
    filter_horizontal = ("interest_tags",)
    readonly_fields = ("unsubscribe_token", "created_at", "updated_at")
