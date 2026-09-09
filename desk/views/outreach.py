"""Everything we believe about one reader's taste, in one place."""

from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from desk.permissions import staff_required
from readers.models import Reader


@staff_required
def reader_tags(request, pk):
    reader = get_object_or_404(Reader, pk=pk)
    return render(request, "desk/reader_tags.html", {
        "page_title": f"Tags for {reader.email}",
        "breadcrumbs": [("Users", reverse("desk:readers_list")),
                        (reader.email, reverse("desk:readers_change", args=[reader.pk])),
                        ("Tags", None)],
        "reader": reader,
        "picked": reader.interest_tags.all().order_by("name"),
        "inferred": reader.ai_inferred_tags.all().order_by("name"),
        "avoid": reader.ai_avoid_tags.all().order_by("name"),
    })
