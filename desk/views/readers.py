from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from campaigns.models import SavedTemplate
from desk.forms import READER_PROFILE_FIELDS, ReaderForm
from opportunities.models import Tag
from desk.permissions import staff_required
from desk.utils import filter_options, paginate, search
from opportunities.models import Category
from recommendations.models import Recommendation
from recommendations.sending import send_issue_for_reader
from readers.models import Reader

BULK_ACTIONS = (
    {"value": "suggest_events", "label": "Suggest events for these",
     "title": "AI searches the web for events matching the interests these users have "
              "most and you have fewest events for, in the area most of them are in"},
    {"value": "save_template", "label": "Save as template",
     "title": "Keep this selection as a template you can run again or put on a "
              "schedule. Nothing is sent"},
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

    # Pick by interest. Several at once, any or all, and an inferred
    # interest counts as well as a picked one - the same rule the
    # matching uses, so this list is who would actually be reached.
    chosen_slugs = request.GET.getlist("tag")
    chosen_tags = list(Tag.objects.filter(slug__in=chosen_slugs)) if chosen_slugs else []
    match_all = request.GET.get("match") == "all"
    if chosen_tags:
        if match_all:
            for tag in chosen_tags:
                qs = qs.filter(Q(interest_tags=tag) | Q(ai_inferred_tags=tag))
            qs = qs.distinct()
        else:
            qs = qs.filter(Q(interest_tags__in=chosen_tags)
                           | Q(ai_inferred_tags__in=chosen_tags)).distinct()

    if request.method == "POST":
        action = request.POST.get("action")
        ids = request.POST.getlist("selected")
        selected = list(Reader.objects.filter(pk__in=ids))
        if not ids:
            messages.warning(request, "Nothing selected.")
        elif action == "suggest_events":
            from opportunities import research
            from recommendations import ai

            if not ai.is_enabled("classify_opportunities"):
                messages.warning(request, "AI is not configured, or event research is "
                                          "switched off in Settings → AI assistance.")
            else:
                started = research.for_readers(Reader.objects.filter(pk__in=ids))
                if started:
                    messages.success(
                        request,
                        f"Searching for events for {', '.join(started)}. This takes a "
                        "minute or two - they arrive under Events as drafts, with the "
                        "pages they came from. Nothing is published until you say so.")
                else:
                    messages.info(request, "These users' interests all have live events "
                                           "already, or research is already running.")
        elif action == "save_template":
            template = SavedTemplate.objects.create(
                name=f"{len(selected)} user{'' if len(selected) == 1 else 's'} - "
                     f"{timezone.localdate():%-d %b}",
                kind=SavedTemplate.Kind.NEWSLETTER, created_by=request.user)
            template.readers.set(selected)
            chosen = Tag.objects.filter(slug__in=request.GET.getlist("tag"))
            template.audience_tags.set(chosen)
            messages.success(request, "Saved as a template. Give it a name, set how "
                                      "often it runs, then Run now or make it Active.")
            return redirect("desk:saved_templates_change", pk=template.pk)
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

    all_tags = (Tag.objects
                .annotate(n=Count("interested_readers", distinct=True))
                .order_by("-n", "name"))
    context = {
        "page_title": "Users",
        "page_blurb": "Everyone signed up. Tick users - or pick interests to select "
                      "everyone who has them - then preview what they'd get and send.",
        "breadcrumbs": [("Users", None)],
        "all_tags": all_tags,
        "chosen_slugs": chosen_slugs,
        "chosen_tags": chosen_tags,
        "match_all": match_all,
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
