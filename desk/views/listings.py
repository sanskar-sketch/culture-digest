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
    qs = Opportunity.objects.annotate(recs_count=Count("recommendations", distinct=True)).prefetch_related("tags")
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
        messages.warning(request, "AI is not configured - set OPENAI_API_KEY to enable this.")
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
    if request.method == "POST":
        if request.POST.get("load_sample_catalogue") is not None:
            return _load_sample_catalogue(request)
        form = OpportunityForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            if not obj.pk and not obj.created_by_id:
                obj.created_by = request.user
            obj.save()
            form.save_m2m()
            messages.success(request, f"Saved “{obj.title}”.")
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
        "campaigns": instance.campaigns.order_by("-created_at") if instance else [],
        "suits": _who_would_like(instance) if instance else None,
    }
    return render(request, "desk/listing_form.html", context)


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
