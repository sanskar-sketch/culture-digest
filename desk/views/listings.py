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
from opportunities.models import Category, Opportunity, Tag

BULK_ACTIONS = (
    {"value": "publish", "label": "Publish",
     "title": "Make the selected listings visible to readers and eligible for matching"},
    {"value": "archive", "label": "Archive",
     "title": "Take the selected listings out of matching, keeping the record"},
    {"value": "back_to_draft", "label": "Back to draft",
     "title": "Un-publish the selected listings"},
    {"value": "suggest", "label": "Suggest tags with AI",
     "title": "Propose a category, interests, price and dials - nothing is saved until "
              "you open the listing and accept it"},
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

    if request.method == "POST":
        action = request.POST.get("action")
        ids = request.POST.getlist("selected")
        selected = Opportunity.objects.filter(pk__in=ids)
        if not ids:
            messages.warning(request, "Nothing selected.")
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
        {"title": "Interests", "param": "tag",
         "options": filter_options(request, "tag", [(t.slug, t.name) for t in tags_in_use])},
    ]
    has_active_filters = any(request.GET.get(g["param"]) for g in filter_groups)

    context = {
        "page_title": "Listings",
        "page_blurb": "Shows, exhibitions, meals, talks, walks - everything the newsletter "
                      "can recommend. A listing stays a draft, invisible to readers, until "
                      "you publish it.",
        "breadcrumbs": [("Listings", None)],
        "page_obj": page_obj,
        "result_count": qs.count(),
        "search_placeholder": "Search listings…",
        "filter_groups": filter_groups,
        "has_active_filters": has_active_filters,
        "bulk_actions": BULK_ACTIONS,
        "add_url": reverse("desk:listings_add"),
    }
    return render(request, "desk/listing_list.html", context)


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
        "page_title": "Add listing" if not instance else f"Edit {instance.title}",
        "breadcrumbs": [("Listings", reverse("desk:listings_list")),
                        ("Add" if not instance else instance.title, None)],
        "form": form,
        "instance": instance,
        "performance": performance,
    }
    return render(request, "desk/listing_form.html", context)


def _load_sample_catalogue(request):
    before = Opportunity.objects.count()
    try:
        call_command("seed_sample_catalogue")
    except Exception as exc:
        messages.error(request, f"Could not load the samples: {exc}")
    else:
        added = Opportunity.objects.count() - before
        if added:
            messages.success(request, f"Added {added} sample listings as drafts. Review "
                                      "them, replace the placeholder booking links, then "
                                      "publish the ones you want.")
        else:
            messages.info(request, "The samples are already loaded - nothing added.")
    return redirect("desk:listings_list")
