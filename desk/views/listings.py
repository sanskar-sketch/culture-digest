from collections import defaultdict

from django.contrib import messages
from django.core.management import call_command
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from desk.forms import OpportunityForm
from desk.permissions import staff_required
from desk.utils import filter_options, paginate, search
from opportunities import research
from opportunities.models import Category, Opportunity, Tag
from readers.models import Reader

BULK_ACTIONS = (
    {"value": "publish", "label": "Publish",
     "title": "Make the selected events visible to readers and eligible for matching"},
    {"value": "archive", "label": "Archive",
     "title": "Take the selected events out of matching, keeping the record"},
    {"value": "back_to_draft", "label": "Back to draft",
     "title": "Un-publish the selected events"},
    {"value": "suggest", "label": "Suggest tags with AI",
     "title": "Propose a category, interests, price and dials - nothing is saved until "
              "you open the event and accept it"},
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
        return "draft", "Draft"
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
        if request.POST.get("load_sample_catalogue") is not None:
            return _load_sample_catalogue(request)  # its button is on this page
        action = request.POST.get("action")
        if action == "suggest_for_readers":
            return _suggest_for_readers(request)  # acts on readers, not a selection
        ids = request.POST.getlist("selected")
        selected = Opportunity.objects.filter(pk__in=ids)
        if not ids:
            messages.warning(request, "Nothing selected.")
        elif action == "publish":
            n = selected.update(status=Opportunity.Status.PUBLISHED)
            messages.success(request, f"{n} event{'' if n == 1 else 's'} marked published.")
        elif action == "archive":
            n = selected.update(status=Opportunity.Status.ARCHIVED)
            messages.success(request, f"{n} event{'' if n == 1 else 's'} marked archived.")
        elif action == "back_to_draft":
            n = selected.update(status=Opportunity.Status.DRAFT)
            messages.success(request, f"{n} event{'' if n == 1 else 's'} marked draft.")
        elif action == "suggest":
            _suggest_classification(request, selected)
        return redirect(f"{request.path}?{request.GET.urlencode()}")

    page_obj = paginate(request, qs.order_by("-created_at"))
    for opp in page_obj:
        opp.state_key, opp.state_label = _live_state(opp, today)
    _attach_audience(page_obj)

    tags_in_use = Tag.objects.filter(opportunities__isnull=False).distinct().order_by("name")
    filter_groups = [
        {"title": "Status", "param": "status",
         "options": filter_options(request, "status", Opportunity.Status.choices)},
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
                      "can recommend. An event stays a draft, invisible to readers, until "
                      "you publish it, and is archived on its own once it has ended.",
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
        "wanted": list(Tag.objects.filter(origin=Tag.Origin.READER, opportunities__isnull=True)
                       .order_by("-times_requested", "name")[:8]),
    }
    return render(request, "desk/listing_list.html", context)


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


def _suggest_classification(request, queryset):
    from recommendations import ai

    if not ai.is_enabled():
        messages.warning(request, "AI is not configured, or event classification is "
                                  "switched off in Settings → AI assistance.")
        return
    for opportunity in queryset[:10]:
        suggestion = ai.classify_opportunity(opportunity)
        if not suggestion:
            messages.warning(request, f"Could not classify “{opportunity.title}”.")
            continue
        messages.info(request, format_html(
            "<strong>{}</strong> — category: <code>{}</code>; interests: <code>{}</code>; "
            "price: <code>{}</code>; mainstream→unusual: <code>{}</code>; "
            "intimate→large-scale: <code>{}</code>. {}",
            opportunity.title, suggestion.get("category", "—"),
            ", ".join(suggestion.get("tags") or []) or "—",
            suggestion.get("price_tier", "—"), suggestion.get("mainstream_to_unusual", "—"),
            suggestion.get("intimate_to_large_scale", "—"), suggestion.get("reasoning", ""),
        ))


@staff_required
def listing_form(request, pk=None):
    instance = get_object_or_404(Opportunity, pk=pk) if pk else None
    filled = None
    if request.method == "POST":
        if request.POST.get("load_sample_catalogue") is not None:
            return _load_sample_catalogue(request)
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


def _load_sample_catalogue(request):
    before = Opportunity.objects.count()
    try:
        call_command("seed_sample_catalogue")
    except Exception as exc:
        messages.error(request, f"Could not load the samples: {exc}")
    else:
        added = Opportunity.objects.count() - before
        if added:
            messages.success(request, f"Added {added} sample events as drafts. Review "
                                      "them, replace the placeholder booking links, then "
                                      "publish the ones you want.")
        else:
            messages.info(request, "The samples are already loaded - nothing added.")
    return redirect("desk:listings_list")
