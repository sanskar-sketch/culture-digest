from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from desk.forms import READER_PROFILE_FIELDS, ReaderForm
from desk.permissions import staff_required
from desk.utils import filter_options, paginate, search
from opportunities.models import Category
from recommendations.models import Recommendation
from recommendations.sending import send_issue_for_reader
from readers.models import Reader

BULK_ACTIONS = (
    {"value": "preview", "label": "Preview newsletter",
     "title": "Dry run - builds what each selected reader would get. Nothing is sent"},
    {"value": "send", "label": "Send newsletter now",
     "title": "Really emails the selected readers, immediately",
     "confirm": "This really emails the selected readers. Send now?"},
    {"value": "interpret", "label": "Interpret taste with AI",
     "title": "Re-read the selected readers' own words and refresh what AI infers "
              "about their taste"},
)


def _profile_completeness(reader):
    filled = 0
    for field in READER_PROFILE_FIELDS:
        value = getattr(reader, field)
        if value not in (None, "", [], {}):
            filled += 1
    total = len(READER_PROFILE_FIELDS) + 2
    if reader.interest_categories:
        filled += 1
    if reader.pk and reader.interest_tags.exists():
        filled += 1
    return round(filled / total * 100)


@staff_required
def reader_list(request):
    qs = Reader.objects.annotate(
        recs_count=Count("issues__recommendations", distinct=True),
        replied_count=Count("issues__recommendations",
                       filter=~Q(issues__recommendations__feedback=Recommendation.Feedback.NONE),
                       distinct=True),
    ).prefetch_related("interest_tags")
    qs = search(qs, request, ["email", "name", "location", "travel_destinations",
                              "loved_examples", "disliked_examples", "notes",
                              "other_categories", "other_interests"])

    active = request.GET.get("active")
    if active in ("1", "0"):
        qs = qs.filter(is_active=(active == "1"))
    follows = request.GET.get("follows")
    if follows == "none":
        qs = qs.filter(interest_categories=[])
    elif follows:
        ids = [r.pk for r in qs.only("pk", "interest_categories") if follows in (r.interest_categories or [])]
        qs = qs.filter(pk__in=ids)
    sent = request.GET.get("sent")
    if sent == "never":
        qs = qs.filter(issues__isnull=True)
    elif sent == "sent":
        qs = qs.filter(issues__sent_at__isnull=False).distinct()
    elif sent == "replied":
        qs = qs.filter(issues__recommendations__feedback__in=[
            Recommendation.Feedback.MORE_LIKE_THIS, Recommendation.Feedback.NOT_FOR_ME,
            Recommendation.Feedback.SAVE, Recommendation.Feedback.BOOKED]).distinct()
    budget = request.GET.get("budget")
    if budget:
        qs = qs.filter(budget=budget)
    travel = request.GET.get("travel")
    if travel:
        qs = qs.filter(travel_radius=travel)
    surprise = request.GET.get("surprise")
    if surprise in ("1", "0"):
        qs = qs.filter(open_to_surprise=(surprise == "1"))

    if request.method == "POST":
        action = request.POST.get("action")
        ids = request.POST.getlist("selected")
        selected = list(Reader.objects.filter(pk__in=ids))
        if not ids:
            messages.warning(request, "Nothing selected.")
        elif action in ("preview", "send"):
            dry_run = action == "preview"
            for reader in selected:
                try:
                    result = send_issue_for_reader(reader, dry_run=dry_run)
                except Exception as exc:
                    messages.error(request, f"{reader.email}: send failed — {exc}")
                    continue
                level = messages.success if (result.sent or dry_run) else messages.warning
                level(request, f"{reader.email}: {result.message}")
        elif action == "interpret":
            from recommendations import ai

            if not ai.is_enabled():
                messages.warning(request, "AI is not configured - set OPENAI_API_KEY to enable this.")
            else:
                done = sum(1 for reader in selected if ai.apply_interpretation(reader))
                failed = len(selected) - done
                messages.success(
                    request,
                    f"Interpreted {done} reader profile{'' if done == 1 else 's'}."
                    + (f" {failed} could not be interpreted - see the logs." if failed else ""))
        return redirect(f"{request.path}?{request.GET.urlencode()}")

    page_obj = paginate(request, qs.order_by("-created_at"))
    for reader in page_obj:
        reader.completeness = _profile_completeness(reader)

    filter_groups = [
        {"title": "Active", "param": "active", "options": filter_options(request, "active", [("1", "Active"), ("0", "Inactive")])},
        {"title": "Follows", "param": "follows", "options": filter_options(request, "follows", list(Category.choices) + [("none", "Nothing picked")])},
        {"title": "Newsletter", "param": "sent", "options": filter_options(request, "sent", [("never", "Never sent anything"), ("sent", "Sent at least once"), ("replied", "Has given feedback")])},
        {"title": "Budget", "param": "budget", "options": filter_options(request, "budget", Reader.Budget.choices)},
        {"title": "Travel radius", "param": "travel", "options": filter_options(request, "travel", Reader.TravelRadius.choices)},
        {"title": "Open to surprise", "param": "surprise", "options": filter_options(request, "surprise", [("1", "Yes"), ("0", "No")])},
    ]
    has_active_filters = any(request.GET.get(g["param"]) for g in filter_groups)

    context = {
        "page_title": "Readers",
        "page_blurb": "Everyone subscribed: what they told us about their taste, what "
                      "they have been sent, and how they responded.",
        "breadcrumbs": [("Readers", None)],
        "page_obj": page_obj,
        "result_count": qs.count(),
        "search_placeholder": "Search readers…",
        "filter_groups": filter_groups,
        "has_active_filters": has_active_filters,
        "bulk_actions": BULK_ACTIONS,
        "delete_kind": "readers",
    }
    return render(request, "desk/reader_list.html", context)


@staff_required
def reader_form(request, pk):
    instance = get_object_or_404(Reader, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("preview", "send", "interpret"):
            if action in ("preview", "send"):
                try:
                    result = send_issue_for_reader(instance, dry_run=(action == "preview"))
                    level = messages.success if (result.sent or action == "preview") else messages.warning
                    level(request, result.message)
                except Exception as exc:
                    messages.error(request, f"Send failed — {exc}")
            else:
                from recommendations import ai

                if not ai.is_enabled():
                    messages.warning(request, "AI is not configured - set OPENAI_API_KEY to enable this.")
                elif ai.apply_interpretation(instance):
                    messages.success(request, "Taste re-interpreted from their free text.")
                else:
                    messages.warning(request, "Could not interpret - see the logs.")
            return redirect("desk:readers_change", pk=pk)

        form = ReaderForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, f"Saved {instance.email}.")
            return redirect("desk:readers_change", pk=pk)
    else:
        form = ReaderForm(instance=instance)

    feedback_counts = dict(
        Recommendation.objects.filter(issue__reader=instance)
        .values_list("feedback").annotate(n=Count("id"))
    )
    labels = dict(Recommendation.Feedback.choices)
    feedback_breakdown = [(labels.get(k, k), n) for k, n in feedback_counts.items() if n]

    issues = instance.issues.prefetch_related("recommendations__opportunity").order_by("-created_at")[:20]

    context = {
        "page_title": instance.name or instance.email,
        "breadcrumbs": [("Readers", reverse("desk:readers_list")), (instance.email, None)],
        "form": form,
        "instance": instance,
        "completeness": _profile_completeness(instance),
        "feedback_breakdown": feedback_breakdown,
        "issues": issues,
    }
    return render(request, "desk/reader_form.html", context)
