from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from desk.forms import TagForm
from desk.permissions import staff_required
from desk.utils import filter_options, paginate, search
from opportunities.models import Category, Tag


@staff_required
def interest_list(request):
    qs = Tag.objects.annotate(
        listings_count=Count("opportunities", distinct=True),
        readers_count=Count("interested_readers", distinct=True),
    )
    qs = search(qs, request, ["name"])
    category = request.GET.get("category")
    if category:
        qs = qs.filter(category=category)

    if request.method == "POST":
        # A category can be changed inline from the list, one row at a time.
        tag = get_object_or_404(Tag, pk=request.POST.get("tag_id"))
        tag.category = request.POST.get("category_value", "")
        tag.save(update_fields=["category"])
        messages.success(request, f"“{tag.name}” moved to {tag.get_category_display() or 'no category'}.")
        return redirect(f"{request.path}?{request.GET.urlencode()}")

    page_obj = paginate(request, qs.order_by("category", "name"), per_page=100)
    context = {
        "page_title": "Interests",
        "page_blurb": "The interests readers pick from when they sign up, and that you "
                      "tag listings with. Where the two overlap is how a listing finds "
                      "its reader.",
        "breadcrumbs": [("Interests", None)],
        "page_obj": page_obj,
        "result_count": qs.count(),
        "search_placeholder": "Search interests…",
        "filter_groups": [
            {"title": "Category", "param": "category", "options": filter_options(request, "category", Category.choices)},
        ],
        "has_active_filters": bool(request.GET.get("category")),
        "categories": Category.choices,
        "add_url": reverse("desk:interests_add"),
    }
    return render(request, "desk/interest_list.html", context)


@staff_required
def interest_form(request, pk=None):
    instance = get_object_or_404(Tag, pk=pk) if pk else None
    if request.method == "POST":
        form = TagForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"Saved “{obj.name}”.")
            return redirect("desk:interests_list")
    else:
        form = TagForm(instance=instance)
    context = {
        "page_title": "Add interest" if not instance else f"Edit {instance.name}",
        "breadcrumbs": [("Interests", reverse("desk:interests_list")),
                        ("Add" if not instance else instance.name, None)],
        "form": form,
        "instance": instance,
    }
    return render(request, "desk/interest_form.html", context)
