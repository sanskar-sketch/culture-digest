from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from desk.forms import TagForm
from desk.permissions import staff_required
from opportunities.models import Tag



@staff_required
def interest_form(request, pk=None):
    instance = get_object_or_404(Tag, pk=pk) if pk else None
    if request.method == "POST":
        form = TagForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"Saved “{obj.name}”.")
            return redirect("desk:listings_list")
    else:
        form = TagForm(instance=instance)
    context = {
        "page_title": "Add interest" if not instance else f"Edit {instance.name}",
        "breadcrumbs": [("Listings", reverse("desk:listings_list")),
                        ("Add" if not instance else instance.name, None)],
        "form": form,
        "instance": instance,
    }
    return render(request, "desk/interest_form.html", context)
