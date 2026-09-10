from collections import defaultdict

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from desk.forms import EventReviewForm, OpportunityForm
from desk.permissions import staff_required
from desk.utils import filter_options, paginate, search
from opportunities import research
from opportunities.models import Category, Opportunity, Tag
from readers.models import Reader

BULK_ACTIONS = (
    {"value": "archive", "label": "Archive",
     "title": "Retire the selected events. They stop being recommended, and what "
              "readers said about them is kept"},
    {"value": "restore", "label": "Put back",
     "title": "Return the selected archived events to circulation"},
)


# How many of the users an event suits are named under its row before the
# rest become a link.
SUITS_SHOWN = 20
# Past this many, "send to all of them" stops being a one-click decision
# anyone should make from a table, so only the Users list is offered.
SUITS_SENDABLE = 100


def _attach_audience(page_obj):
    """Who each event on this page is good for, by name.

    "Good for 4 users" and the four users named under it have to be the
    same four, so both come from one rule: an active reader who picked, or
    was read as having, any of the event's interests. It is the rule the
    Users list filters by and the rule the matching scores on, so the
    number, the names and the page behind the link all agree.

    Three queries for the whole page rather than one per row: the two
    interest tables, for the tags on this page, then the readers they name.
    """
    events = list(page_obj)
    for event in events:
        event.suits, event.good_for, event.suits_extra = [], 0, 0
        event.suits_url, event.send_url = None, None

    tag_ids = {tag.pk for event in events for tag in event.tags.all()}
    if not tag_ids:
        return

    readers_by_tag = defaultdict(set)
    picked = set()
    for through, is_picked in ((Reader.interest_tags.through, True),
                               (Reader.ai_inferred_tags.through, False)):
        rows = through.objects.filter(tag_id__in=tag_ids, reader__is_active=True)
        for tag_id, reader_id in rows.values_list("tag_id", "reader_id"):
            readers_by_tag[tag_id].add(reader_id)
            if is_picked:
                picked.add((reader_id, tag_id))

    named = {pk for ids in readers_by_tag.values() for pk in ids}
    readers = list(Reader.objects.filter(pk__in=named).order_by("email"))

    for event in events:
        tags = list(event.tags.all())
        if not tags:
            continue
        theirs = set().union(*(readers_by_tag[t.pk] for t in tags))
        event.good_for = len(theirs)
        if not theirs:
            continue
        event.suits_url = "{}?{}".format(
            reverse("desk:readers_list"), "&".join(f"tag={t.slug}" for t in tags))
        for reader in readers:
            if reader.pk not in theirs:
                continue
            if len(event.suits) == SUITS_SHOWN:
                break
            event.suits.append({
                "reader": reader,
                "picked": [t.name for t in tags if (reader.pk, t.pk) in picked],
                "inferred": [t.name for t in tags
                             if reader.pk in readers_by_tag[t.pk]
                             and (reader.pk, t.pk) not in picked],
            })
        event.suits_extra = event.good_for - len(event.suits)
        if event.good_for <= SUITS_SENDABLE:
            ids = ",".join(str(r.pk) for r in readers if r.pk in theirs)
            event.send_url = f"{reverse('desk:send')}?r={ids}&e={event.pk}"


def _live_state(opp, today):
    if opp.status == Opportunity.Status.PUBLISHED:
        if opp.end_date and opp.end_date < today:
            return "ended", "Ended"
        return "live", "Live"
    if opp.status == Opportunity.Status.DRAFT:
        # Not a state anyone puts an event in any more: it means AI found
        # this and nobody has said yes or no to it yet.
        return "draft", "Waiting for you"
    return "archived", "Archived"


@staff_required
def listing_list(request):
    today = timezone.localdate()
    qs = Opportunity.objects.annotate(
        recs_count=Count("recommendations", distinct=True),
    ).prefetch_related("tags")
    qs = search(qs, request, ["title", "description", "editorial_note", "location_area",
                              "location_name", "tags__name"])

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
    tag = request.GET.get("tag")
    if tag:
        qs = qs.filter(tags__slug=tag)
    origin = request.GET.get("origin")
    if origin == "ai":
        qs = qs.filter(found_by_ai=True)
    elif origin == "editor":
        qs = qs.filter(found_by_ai=False)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "suggest_for_readers":
            return _suggest_for_readers(request)  # acts on readers, not a selection
        ids = request.POST.getlist("selected")
        selected = Opportunity.objects.filter(pk__in=ids)
        if not ids:
            messages.warning(request, "Nothing selected.")
        elif action == "archive":
            n = selected.update(status=Opportunity.Status.ARCHIVED)
            messages.success(request, f"{n} event{'' if n == 1 else 's'} retired. They "
                                      "stop being recommended; what readers said is kept.")
        elif action == "restore":
            n = selected.update(status=Opportunity.Status.PUBLISHED)
            messages.success(request, f"{n} event{'' if n == 1 else 's'} back in circulation.")
        return redirect(f"{request.path}?{request.GET.urlencode()}")

    page_obj = paginate(request, qs.order_by("-created_at"))
    for opp in page_obj:
        opp.state_key, opp.state_label = _live_state(opp, today)
    _attach_audience(page_obj)

    tags_in_use = Tag.objects.filter(opportunities__isnull=False).distinct().order_by("name")
    filter_groups = [
        {"title": "Status", "param": "status",
         "options": filter_options(request, "status", [
             (Opportunity.Status.PUBLISHED, "In circulation"),
             (Opportunity.Status.DRAFT, "Waiting for you"),
             (Opportunity.Status.ARCHIVED, "Archived")])},
        {"title": "Dates", "param": "live",
         "options": filter_options(request, "live", [("live", "Still on"), ("ended", "Ended"), ("undated", "No dates")])},
        {"title": "Category", "param": "category", "options": filter_options(request, "category", Category.choices)},
        {"title": "Price tier", "param": "price_tier",
         "options": filter_options(request, "price_tier", Opportunity.PriceTier.choices)},
        {"title": "Online", "param": "online",
         "options": filter_options(request, "online", [("1", "Online"), ("0", "In person")])},
        {"title": "Where it came from", "param": "origin",
         "options": filter_options(request, "origin", [
             ("ai", "Found by AI — needs checking"), ("editor", "Written by an editor")])},
        {"title": "Interests", "param": "tag",
         "options": filter_options(request, "tag", [(t.slug, t.name) for t in tags_in_use])},
    ]
    has_active_filters = any(request.GET.get(g["param"]) for g in filter_groups)

    context = {
        "page_title": "Events",
        "page_blurb": "Shows, exhibitions, meals, talks, walks - everything the newsletter "
                      "can recommend. What you add here is in circulation straight away; "
                      "what AI finds waits for you to accept it. An event archives itself "
                      "once it has ended.",
        "breadcrumbs": [("Events", None)],
        "page_obj": page_obj,
        "result_count": qs.count(),
        "search_placeholder": "Search events…",
        "filter_groups": filter_groups,
        "has_active_filters": has_active_filters,
        "bulk_actions": BULK_ACTIONS,
        "delete_kind": "listings",
        "add_url": reverse("desk:listings_add"),
        "readers_total": Reader.objects.filter(is_active=True).count(),
        "waiting": Opportunity.objects.filter(status=Opportunity.Status.DRAFT).count(),
        "wanted": list(Tag.objects.filter(origin=Tag.Origin.READER, opportunities__isnull=True)
                       .order_by("-times_requested", "name")[:8]),
    }
    return render(request, "desk/listing_list.html", context)


@staff_required
def listing_review(request):
    """What AI found, waiting for a person to say yes or no.

    Research puts everything it finds here rather than into circulation.
    Each card is editable, because AI gets a date or a price wrong often
    enough that "accept or reject" alone would throw away good events. The
    pages it read are shown beside it, so a claim can be checked before it
    is believed.

    Accepting saves your corrections and puts the event live. Rejecting
    deletes it - nothing has been sent from it, so there is nothing to keep.
    """
    waiting = (Opportunity.objects.filter(status=Opportunity.Status.DRAFT)
               .prefetch_related("tags").order_by("-created_at"))

    if request.method == "POST":
        ids = request.POST.getlist("selected")
        action = request.POST.get("action")
        if request.POST.get("accept_one"):
            return _accept_one(request, request.POST["accept_one"])
        if not ids:
            messages.warning(request, "Nothing selected.")
        elif action == "accept":
            n = waiting.filter(pk__in=ids).update(status=Opportunity.Status.PUBLISHED)
            messages.success(request, f"{n} event{'' if n == 1 else 's'} accepted and live. "
                                      "They can be recommended from now on.")
        elif action == "reject":
            n, _ = waiting.filter(pk__in=ids).delete()
            messages.success(request, "Rejected. Those events are gone.")
        return redirect("desk:listings_review")

    # Ten at a time. A review is a considered thing, and a page of sixty
    # editable cards is one nobody finishes.
    page_obj = paginate(request, waiting, per_page=10)
    cards = []
    for event in page_obj:
        form = EventReviewForm(instance=event, prefix=str(event.pk))
        for field in form.fields.values():
            field.widget.attrs["form"] = f"accept-{event.pk}"
        cards.append({
            "event": event, "form": form,
            "sources": [src for src in (event.sources or []) if isinstance(src, dict)],
        })

    return render(request, "desk/listing_review.html", {
        "page_title": "Waiting for you",
        "page_blurb": "Events AI found, and anything else not yet in circulation. "
                      "Correct what it got wrong, then accept or reject. Nothing here "
                      "can reach a reader until you accept it.",
        "breadcrumbs": [("Events", reverse("desk:listings_list")), ("Waiting", None)],
        "cards": cards,
        "page_obj": page_obj,
        "result_count": waiting.count(),
        "bulk_actions": [
            {"value": "accept", "label": "Accept selected",
             "title": "Put the ticked events into circulation, as they are"},
            {"value": "reject", "label": "Reject selected",
             "title": "Delete the ticked events",
             "confirm": "Reject these? They are deleted, not archived."},
        ],
    })


def _accept_one(request, pk):
    """Accept one card, keeping whatever the editor corrected on it."""
    event = get_object_or_404(Opportunity, pk=pk, status=Opportunity.Status.DRAFT)
    form = EventReviewForm(request.POST, instance=event, prefix=str(event.pk))
    if not form.is_valid():
        messages.error(request, format_html(
            "Could not accept “{}”: {}", event.title,
            "; ".join(f"{f}: {' '.join(e)}" for f, e in form.errors.items())))
        return redirect("desk:listings_review")
    saved = form.save(commit=False)
    saved.status = Opportunity.Status.PUBLISHED
    saved.save()
    form.save_m2m()
    messages.success(request, f"“{saved.title}” accepted and live."
                              + ("" if saved.tags.exists() else
                                 " It has no interests, so it can't reach anyone yet - "
                                 "open it and tick some."))
    return redirect("desk:listings_review")


def _suggest_for_readers(request):
    """Let AI go and find events for the interests your readers actually have.

    Driven by the reader data, not by a chosen interest: the interests most
    readers picked that have the fewest live events, in the area most
    readers are in. Runs off the request; drafts appear as they are found.
    """
    from recommendations import ai

    if not ai.is_enabled("classify_opportunities"):
        messages.warning(request, "AI is not configured, or event research is switched "
                                  "off in Settings → AI assistance.")
        return redirect("desk:listings_list")
    started = research.for_readers()
    if started:
        messages.success(
            request,
            f"Searching for events for {', '.join(started)} - the interests your readers "
            "have most and you have least for. This takes a minute or two; refresh and "
            "anything found will be here as a draft with the pages it read. Nothing is "
            "published until you say so.")
    else:
        messages.info(request, "Nothing to search for: every interest your readers have "
                               "already has live events, or research is already running.")
    return redirect("desk:listings_list")


@staff_required
def listing_form(request, pk=None):
    instance = get_object_or_404(Opportunity, pk=pk) if pk else None
    filled = None
    if request.method == "POST":
        if request.POST.get("fill_from") is not None:
            filled = _fill_with_ai(request)
            form = OpportunityForm(initial=filled or None)
        else:
            form = OpportunityForm(request.POST, instance=instance)
            if form.is_valid():
                creating = not instance
                obj = form.save(commit=False)
                if not obj.pk and not obj.created_by_id:
                    obj.created_by = request.user
                if creating:
                    # Saving an event is the decision to run it. What AI
                    # finds is the only thing that waits, and it waits on
                    # the review screen.
                    obj.status = Opportunity.Status.PUBLISHED
                obj.save()
                form.save_m2m()
                messages.success(request, f"Saved “{obj.title}”.")
                if creating and not obj.tags.exists():
                    _tag_on_creation(request, obj)
                if "save_add_another" in request.POST:
                    return redirect("desk:listings_add")
                return redirect("desk:listings_change", pk=obj.pk)
    else:
        form = OpportunityForm(instance=instance)

    performance = None
    if instance:
        from recommendations.models import Recommendation

        performance = instance.recommendations.aggregate(
            sent=Count("id"),
            booked=Count("id", filter=Q(feedback=Recommendation.Feedback.BOOKED)),
            saved=Count("id", filter=Q(feedback=Recommendation.Feedback.SAVE)),
            more=Count("id", filter=Q(feedback=Recommendation.Feedback.MORE_LIKE_THIS)),
            nope=Count("id", filter=Q(feedback=Recommendation.Feedback.NOT_FOR_ME)),
        )

    context = {
        "page_title": "Add event" if not instance else f"Edit {instance.title}",
        "breadcrumbs": [("Events", reverse("desk:listings_list")),
                        ("Add" if not instance else instance.title, None)],
        "form": form,
        "instance": instance,
        "performance": performance,
        "suits": _who_would_like(instance) if instance else None,
        "filled": filled,
    }
    return render(request, "desk/listing_form.html", context)


def _tag_on_creation(request, event):
    """A new event gets its interests straight away, so it can reach someone.

    Only when the editor left them empty - a choice they made is kept - and
    only the interests: category, price and the dials change what a reader
    is told, so those stay a suggestion in the message.
    """
    from recommendations import ai

    if not ai.is_enabled("classify_opportunities"):
        return
    suggestion = ai.classify_opportunity(event)
    tags = list(Tag.objects.filter(slug__in=(suggestion or {}).get("tags") or []))
    if tags:
        event.tags.set(tags)
        messages.info(request, "Tagged it: " + ", ".join(t.name for t in tags)
                      + ". Change them below if that's wrong.")
    else:
        messages.info(request, "AI couldn't suggest interests for this one - tick some "
                               "below so it can reach someone.")


def _fill_with_ai(request):
    """Turn a name and a place into a filled-in form, from the live web.

    One event, one search, inside the request - so a short ceiling, and
    the honest fallback is "couldn't find it", never a made-up entry.
    """
    from recommendations import ai

    what = (request.POST.get("fill_from") or "").strip()
    area = (request.POST.get("fill_area") or "").strip()
    if not what:
        messages.warning(request, "Say what the event is first.")
        return None
    if not ai.is_enabled("classify_opportunities"):
        messages.warning(request, "AI is not configured, or event research is switched "
                                  "off in Settings → AI assistance.")
        return None
    found = ai.research_listings(what, area=area, count=1, timeout=22.0)
    rows = (found or {}).get("listings") or []
    if not rows:
        messages.warning(request, f"Couldn't find “{what}” anywhere reliable. Fill it in by "
                                  "hand, or try with the venue or city added.")
        return None
    row = rows[0]
    from opportunities.research import _choice, _date, _dial

    urls = [str(s.get("url", "")) for s in row.get("sources") or []
            if isinstance(s, dict) and str(s.get("url", "")).startswith("http")]

    initial = {
        "title": row.get("title") or what,
        "description": row.get("description", ""),
        "category": _choice(row.get("category"), Category.choices, ""),
        "price_tier": _choice(row.get("price_tier"), Opportunity.PriceTier.choices, ""),
        "price_display": row.get("price_display", ""),
        "location_name": row.get("location_name", ""),
        "location_area": row.get("location_area", "") or area,
        "booking_url": row.get("booking_url", ""),
        "start_date": _date(row.get("start_date")),
        "end_date": _date(row.get("end_date")),
        "mainstream_to_unusual": _dial(row.get("mainstream_to_unusual")),
        "intimate_to_large_scale": _dial(row.get("intimate_to_large_scale")),
        "tags": list(Tag.objects.filter(slug__in=row.get("tags") or []).values_list("pk", flat=True)),
        "editorial_note": ("Filled in by AI from: " + "; ".join(urls)) if urls else
                          "Filled in by AI, which did not name the pages it read - "
                          "check every field before saving.",
    }
    messages.success(request, (
        f"Filled in from {len(urls)} page(s) - check the date and price, then save. "
        "Nothing is saved yet." if urls else
        "Filled it in, but AI didn't say which pages it read - check every field "
        "against the venue before saving. Nothing is saved yet."))
    return initial


def _who_would_like(event):
    """Readers this event could reach, from the interests it carries.

    The vice-versa of "find events for these readers": the interests on the
    event are the audience rule, and the count is who that rule matches
    right now. If the event has no interests yet there is no rule, and the
    answer is honestly nobody.
    """
    tags = list(event.tags.all())
    if not tags:
        return {"tags": [], "count": 0, "url": None}
    readers = Reader.objects.filter(is_active=True).filter(
        Q(interest_tags__in=tags) | Q(ai_inferred_tags__in=tags)).distinct()
    query = "&".join(f"tag={t.slug}" for t in tags)
    return {"tags": tags, "count": readers.count(),
            "url": f"{reverse('desk:readers_list')}?{query}"}


@staff_required
def listing_suggest_audience(request, pk):
    """Let AI tag the event, so "who would like this" has something to go on.

    Applied, not printed: interests on an event are visible on the form,
    saved with it, and trivially undone. The category, price and dials are
    still only suggested in a message - those change what a reader is told.
    """
    from django.http import HttpResponseNotAllowed

    from recommendations import ai

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    event = get_object_or_404(Opportunity, pk=pk)
    if not ai.is_enabled("classify_opportunities"):
        messages.warning(request, "AI is not configured, or event classification is "
                                  "switched off in Settings → AI assistance.")
        return redirect("desk:listings_change", pk=pk)
    suggestion = ai.classify_opportunity(event)
    if not suggestion:
        messages.warning(request, "Could not work out who this suits - see Settings → Health.")
        return redirect("desk:listings_change", pk=pk)
    tags = list(Tag.objects.filter(slug__in=suggestion.get("tags") or []))
    if tags:
        event.tags.add(*tags)
    suits = _who_would_like(event)
    messages.success(
        request,
        f"Tagged {', '.join(t.name for t in tags) or 'nothing new'}. "
        f"{suits['count']} reader{'' if suits['count'] == 1 else 's'} would be reached. "
        + (f"Also suggested: category {suggestion.get('category', '—')}, "
           f"price {suggestion.get('price_tier', '—')} - change those below if they fit.")
    )
    return redirect("desk:listings_change", pk=pk)
