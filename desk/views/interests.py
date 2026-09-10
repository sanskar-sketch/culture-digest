from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from desk.forms import TagForm
from desk.permissions import staff_required
from desk.utils import filter_options, paginate, search
from opportunities import research
from opportunities.models import Category, Opportunity, Tag

BULK_ACTIONS = (
    {"value": "research", "label": "Find events with AI",
     "title": "Search the web for real events matching the selected interests. "
              "Everything found arrives as a draft event with its sources"},
)


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

    origin = request.GET.get("origin")
    if origin in (Tag.Origin.READER, Tag.Origin.EDITOR):
        qs = qs.filter(origin=origin)
    coverage = request.GET.get("coverage")
    if coverage == "empty":
        qs = qs.filter(opportunities__isnull=True)
    elif coverage == "covered":
        qs = qs.filter(opportunities__isnull=False).distinct()

    if request.method == "POST":
        if request.POST.get("action") == "research":
            return _research(request)
        # A category can be changed inline from the list, one row at a time.
        tag = get_object_or_404(Tag, pk=request.POST.get("tag_id"))
        tag.category = request.POST.get("category_value", "")
        tag.save(update_fields=["category"])
        messages.success(request, f"“{tag.name}” moved to {tag.get_category_display() or 'no category'}.")
        return redirect(f"{request.path}?{request.GET.urlencode()}")

    # What readers asked for and we have nothing for, first: that is the
    # queue, and it is the only ordering an editor actually wants.
    page_obj = paginate(
        request,
        qs.order_by("-times_requested", "listings_count", "category", "name"),
        per_page=100)
    for tag in page_obj:
        tag.researching = research.is_running(tag)
    context = {
        "page_title": "Interests",
        "page_blurb": "The interests readers pick from when they sign up, and that you "
                      "tag events with. Where the two overlap is how an event finds "
                      "its reader. Interests readers typed in themselves arrive here on "
                      "their own.",
        "breadcrumbs": [("Settings", reverse("desk:siteconfig")), ("Interests", None)],
        "page_obj": page_obj,
        "result_count": qs.count(),
        "search_placeholder": "Search interests…",
        "filter_groups": [
            {"title": "Category", "param": "category",
             "options": filter_options(request, "category", Category.choices)},
            {"title": "Where it came from", "param": "origin",
             "options": filter_options(request, "origin", [
                 (Tag.Origin.READER, "Readers asked for it"),
                 (Tag.Origin.EDITOR, "An editor added it")])},
            {"title": "Events", "param": "coverage",
             "options": filter_options(request, "coverage", [
                 ("empty", "Nothing to send yet"), ("covered", "Has events")])},
        ],
        "has_active_filters": any(request.GET.get(p) for p in
                                  ("category", "origin", "coverage")),
        "categories": Category.choices,
        "bulk_actions": BULK_ACTIONS,
        "delete_kind": "interests",
        "wanted_count": Tag.objects.filter(origin=Tag.Origin.READER,
                                           opportunities__isnull=True).count(),
        "add_url": reverse("desk:interests_add"),
    }
    return render(request, "desk/interest_list.html", context)


def _research(request):
    """Kick off web research for the ticked interests.

    Off the request: searching takes tens of seconds and a page render has
    far less than that before the worker is killed.
    """
    from recommendations import ai

    ids = request.POST.getlist("selected")
    tags = list(Tag.objects.filter(pk__in=ids))
    if not tags:
        messages.warning(request, "Nothing selected.")
    elif not ai.is_enabled("classify_opportunities"):
        messages.warning(request, "AI is not configured, or event research is "
                                  "switched off in Settings → AI assistance.")
    else:
        area = (request.POST.get("area") or "").strip()
        started = [tag.name for tag in tags if research.start(tag, area=area)]
        if started:
            messages.success(
                request,
                f"Searching for {', '.join(started)}. This takes a minute or two - "
                "refresh Events and anything found will be there as a draft, with "
                "the pages it read. Nothing is published until you say so.")
        busy = len(tags) - len(started)
        if busy:
            messages.info(request, f"{busy} already being researched - leave them running.")
    return redirect(f"{request.path}?{request.GET.urlencode()}")


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
