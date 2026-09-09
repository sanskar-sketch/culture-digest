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
