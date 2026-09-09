"""The catalogue: listings grouped under the interests that reach readers.

A listing and an interest are different things - one is an event, the
other a label - but an editor only ever cares about them together: an
interest is worth nothing without a listing under it, and a listing
nobody can be matched to is worth nothing at all. So they are one
screen, with the interest as the heading and its listings beneath.

The ordering is the work queue. Interests readers asked for with nothing
to send come first; then the catalogue proper, fullest interest first;
then the interests an editor seeded that nothing carries yet. Untagged
listings - reachable by nobody - are called out above all of it.

One form carries two kinds of tick box: `selected` for listings, and
`selected_interest` for interests, so Publish and Find listings with AI
share a bulk bar without ambiguity.
"""

from django.contrib import messages
from django.core.management import call_command
from django.db.models import Case, Count, IntegerField, Prefetch, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from desk.permissions import staff_required
from desk.utils import filter_options, paginate
from opportunities import research
from opportunities.models import Category, Opportunity, Tag

from .listings import BULK_ACTIONS as LISTING_ACTIONS, _live_state, _suggest_classification

INTEREST_ACTIONS = (
    {"value": "research", "label": "Find listings with AI",
     "title": "Search the web for real events matching the ticked interests. Everything "
              "found arrives as a draft, with its sources"},
)

PER_PAGE = 20


def _listing_filters(request, qs, today):
    """The listing-level filters, applied to a listing queryset."""
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q)
                       | Q(editorial_note__icontains=q) | Q(location_area__icontains=q)
                       | Q(location_name__icontains=q))
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    live = request.GET.get("live")
    if live == "live":
        qs = qs.filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
    elif live == "ended":
        qs = qs.filter(end_date__lt=today)
    elif live == "undated":
        qs = qs.filter(start_date__isnull=True, end_date__isnull=True)
    category = request.GET.get("category")
    if category:
        qs = qs.filter(category=category)
    price_tier = request.GET.get("price_tier")
    if price_tier:
        qs = qs.filter(price_tier=price_tier)
    online = request.GET.get("online")
    if online in ("1", "0"):
        qs = qs.filter(is_online=(online == "1"))
    origin = request.GET.get("origin")
    if origin == "ai":
        qs = qs.filter(found_by_ai=True)
    elif origin == "editor":
        qs = qs.filter(found_by_ai=False)
    return qs


LISTING_PARAMS = ("q", "status", "live", "category", "price_tier", "online", "origin")


@staff_required
def catalogue(request):
    today = timezone.localdate()

    if request.method == "POST":
        return _act(request)

    narrowing = any(request.GET.get(p) for p in LISTING_PARAMS)
    listings = _listing_filters(
        request,
        Opportunity.objects.annotate(recs_count=Count("recommendations", distinct=True))
        .order_by("-created_at"),
        today,
    )

    # Interests, with only the listings that survive the listing filters
    # attached to each - so a search for "jazz" shows the jazz interest
    # with its jazz listings, not every listing it has.
    interests = Tag.objects.annotate(
        listings_total=Count("opportunities", distinct=True),
        readers_count=Count("interested_readers", distinct=True),
    ).prefetch_related(Prefetch("opportunities", queryset=listings, to_attr="matching"))

    q = (request.GET.get("q") or "").strip()
    if q:
        # An interest shows if its own name matches, or any listing under it does.
        interests = interests.filter(Q(name__icontains=q) | Q(opportunities__in=listings)).distinct()
    interest_origin = request.GET.get("interest_origin")
    if interest_origin in (Tag.Origin.READER, Tag.Origin.EDITOR):
        interests = interests.filter(origin=interest_origin)
    coverage = request.GET.get("coverage")
    if coverage == "empty":
        interests = interests.filter(opportunities__isnull=True)
    elif coverage == "covered":
        interests = interests.filter(opportunities__isnull=False).distinct()

    # Three bands. What readers asked for and cannot yet be sent, first -
    # that is the work. Then the catalogue proper, fullest interest first.
    # Then everything an editor seeded that nothing carries yet: real, but
    # not what anyone opened this page to look at.
    interests = interests.annotate(
        has_listings=Case(When(listings_total__gt=0, then=Value(1)), default=Value(0),
                          output_field=IntegerField()),
    ).order_by("-times_requested", "-has_listings", "-listings_total", "category", "name")
    page_obj = paginate(request, interests, per_page=PER_PAGE)

    groups = []
    for tag in page_obj:
        rows = list(tag.matching)
        if narrowing and not rows and coverage != "empty":
            continue  # nothing under it matches what you asked for
        for opp in rows:
            opp.state_key, opp.state_label = _live_state(opp, today)
        tag.researching = research.is_running(tag)
        groups.append({"tag": tag, "listings": rows})

    # Reachable by nobody. Shown once, at the top, only when there are any.
    untagged = []
    if not request.GET.get("page"):
        untagged = list(listings.filter(tags__isnull=True))
        for opp in untagged:
            opp.state_key, opp.state_label = _live_state(opp, today)

    filter_groups = [
        {"title": "Status", "param": "status",
         "options": filter_options(request, "status", Opportunity.Status.choices)},
        {"title": "Dates", "param": "live",
         "options": filter_options(request, "live", [("live", "Still on"), ("ended", "Ended"),
                                                     ("undated", "No dates")])},
        {"title": "Category", "param": "category",
         "options": filter_options(request, "category", Category.choices)},
        {"title": "Price tier", "param": "price_tier",
         "options": filter_options(request, "price_tier", Opportunity.PriceTier.choices)},
        {"title": "Online", "param": "online",
         "options": filter_options(request, "online", [("1", "Online"), ("0", "In person")])},
        {"title": "Listing came from", "param": "origin",
         "options": filter_options(request, "origin", [
             ("ai", "Found by AI — needs checking"), ("editor", "Written by an editor")])},
        {"title": "Interest came from", "param": "interest_origin",
         "options": filter_options(request, "interest_origin", [
             (Tag.Origin.READER, "Readers asked for it"), (Tag.Origin.EDITOR, "An editor added it")])},
        {"title": "Interest has listings", "param": "coverage",
         "options": filter_options(request, "coverage", [
             ("empty", "Nothing to send yet"), ("covered", "Has listings")])},
    ]

    context = {
        "page_title": "Listings",
        "page_blurb": "Everything the newsletter can recommend, grouped by the interest "
                      "that reaches readers. A listing stays a draft, invisible to "
                      "readers, until you publish it.",
        "breadcrumbs": [("Listings", None)],
        "groups": groups,
        "untagged": untagged,
        "page_obj": page_obj,
        "result_count": listings.count(),
        "interest_count": interests.count(),
        "search_placeholder": "Search listings and interests…",
        "filter_groups": filter_groups,
        "has_active_filters": any(request.GET.get(g["param"]) for g in filter_groups),
        "listing_actions": LISTING_ACTIONS,
        "interest_actions": INTEREST_ACTIONS,
        "categories": Category.choices,
        "wanted_count": Tag.objects.filter(origin=Tag.Origin.READER,
                                           opportunities__isnull=True).count(),
        "add_url": reverse("desk:listings_add"),
        "add_interest_url": reverse("desk:interests_add"),
    }
    return render(request, "desk/catalogue.html", context)


def _act(request):
    """Every POST the page can make, routed by what was ticked or pressed."""
    back = f"{request.path}?{request.GET.urlencode()}" if request.GET else request.path
    action = request.POST.get("action")

    if request.POST.get("load_sample_catalogue") is not None:
        from .listings import _load_sample_catalogue

        return _load_sample_catalogue(request)

    # One interest's category, changed inline from its heading.
    if request.POST.get("tag_id"):
        tag = get_object_or_404(Tag, pk=request.POST["tag_id"])
        tag.category = request.POST.get("category_value", "")
        tag.save(update_fields=["category"])
        messages.success(request, f"“{tag.name}” moved to "
                                  f"{tag.get_category_display() or 'no category'}.")
        return redirect(back)

    if action == "research":
        return _research(request, back)

    ids = request.POST.getlist("selected")
    selected = Opportunity.objects.filter(pk__in=ids)
    if not ids:
        messages.warning(request, "Tick a listing first.")
    elif action == "publish":
        n = selected.update(status=Opportunity.Status.PUBLISHED)
        messages.success(request, f"{n} listing{'' if n == 1 else 's'} marked published.")
    elif action == "archive":
        n = selected.update(status=Opportunity.Status.ARCHIVED)
        messages.success(request, f"{n} listing{'' if n == 1 else 's'} marked archived.")
    elif action == "back_to_draft":
        n = selected.update(status=Opportunity.Status.DRAFT)
        messages.success(request, f"{n} listing{'' if n == 1 else 's'} marked draft.")
    elif action == "suggest":
        _suggest_classification(request, selected)
    return redirect(back)


def _research(request, back):
    from recommendations import ai

    ids = request.POST.getlist("selected_interest")
    tags = list(Tag.objects.filter(pk__in=ids))
    if not tags:
        messages.warning(request, "Tick an interest first - the box beside its name.")
    elif not ai.is_enabled("classify_opportunities"):
        messages.warning(request, "AI is not configured, or listing research is switched "
                                  "off in Settings → AI assistance.")
    else:
        area = (request.POST.get("area") or "").strip()
        started = [tag.name for tag in tags if research.start(tag, area=area)]
        if started:
            messages.success(
                request,
                f"Searching for {', '.join(started)}. This takes a minute or two - refresh "
                "and anything found will be here as a draft, with the pages it read. "
                "Nothing is published until you say so.")
        busy = len(tags) - len(started)
        if busy:
            messages.info(request, f"{busy} already being researched - leave them running.")
    return redirect(back)


@staff_required
def interests_redirect(request):
    """The Interests tab is gone; its filters carry over to the grouped page."""
    query = request.GET.copy()
    if "origin" in query:  # the old name for the interest-origin filter
        query["interest_origin"] = query.pop("origin")[0]
    url = reverse("desk:listings_list")
    return redirect(f"{url}?{query.urlencode()}" if query else url)
