from django.contrib import admin

from .models import Reader


@admin.register(Reader)
class ReaderAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "name",
        "location",
        "budget",
        "travel_radius",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "budget", "travel_radius", "location")
    search_fields = ("email", "name", "location")
    filter_horizontal = ("interest_tags",)
    readonly_fields = ("unsubscribe_token", "created_at", "updated_at")
