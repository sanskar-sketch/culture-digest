from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from campaigns.models import Campaign, CampaignDelivery
from campaigns.sending import WEB_BUDGET_SECONDS, preview_for, send_campaign, send_test
from opportunities.models import Opportunity
from desk.forms import CampaignForm
from desk.permissions import staff_required
from desk.utils import filter_options, paginate, search
from readers.models import Reader

BULK_ACTIONS = (
    {"value": "schedule", "label": "Schedule",
     "title": "Queue for the campaign's send time, or the scheduler's next pass if it "
              "has none",
     "confirm": "Scheduled campaigns really email their audience. Schedule now?"},
    {"value": "send_now", "label": "Send now",
     "title": "Skip the queue and start sending immediately",
     "confirm": "This really emails the audience now. Send?"},
    {"value": "cancel", "label": "Cancel",
     "title": "Stop a scheduled or in-progress campaign - anyone not yet emailed won't be"},
    {"value": "back_to_draft", "label": "Back to draft",
     "title": "Return the selected campaigns to draft"},
    {"value": "retry_failed", "label": "Retry failed",
     "title": "Queue failed or stuck deliveries again"},
)


def _progress(campaign):
    counts = dict(campaign.deliveries.values_list("status").annotate(n=Count("id")))
    return _report(counts)


def _report(counts):
    report = {value: counts.get(value, 0) for value in CampaignDelivery.Status.values}
    report["total"] = sum(counts.values())
    return report


def _progress_map(campaigns):
    """Delivery counts for a whole page of campaigns in one query.

    Called per row this was the list's worst cost: with the database a
    continent away, every extra query is a further ~230ms on the page.
    """
    rows = (CampaignDelivery.objects
            .filter(campaign__in=campaigns)
            .values("campaign_id", "status")
            .annotate(n=Count("id")))
    per_campaign = {}
    for row in rows:
        per_campaign.setdefault(row["campaign_id"], {})[row["status"]] = row["n"]
    return {c.pk: _report(per_campaign.get(c.pk, {})) for c in campaigns}


def _audience_sizes(campaigns):
    """How many readers each campaign reaches, without a query per row.

    A campaign with no restrictions reaches every active reader, which is
    one count shared by all of them. Only a campaign that actually narrows
    its audience has to be counted on its own.
    """
    unrestricted = Reader.objects.filter(is_active=True).count()
    sizes = {}
    for campaign in campaigns:
        # .all() reads the prefetch cache; .exists() would query again.
        narrowed = (campaign.audience_location
                    or campaign.audience_categories
                    or list(campaign.audience_tags.all()))
        sizes[campaign.pk] = campaign.audience().count() if narrowed else unrestricted
    return sizes


def _do_schedule(request, campaign) -> bool:
    try:
        campaign.full_clean()
    except Exception as exc:
        messages.error(request, f"{campaign}: can't schedule - {exc}")
        return False
    campaign.status = Campaign.Status.SCHEDULED
    campaign.save(update_fields=["status", "updated_at"])
    when = (f"for {timezone.localtime(campaign.send_at):%a %-d %b, %H:%M}"
            if campaign.send_at else "for the scheduler's next pass (within a few minutes)")
    messages.success(request, f"{campaign}: scheduled {when}.")
    return True


def _do_send_now(request, campaign):
    if campaign.status not in (Campaign.Status.SCHEDULED, Campaign.Status.SENDING):
        try:
            campaign.full_clean()
        except Exception as exc:
            messages.error(request, f"{campaign}: can't send - {exc}")
            return
        campaign.status = Campaign.Status.SCHEDULED
        campaign.send_at = None
        campaign.save(update_fields=["status", "send_at", "updated_at"])
    run = send_campaign(campaign, budget_seconds=WEB_BUDGET_SECONDS)
    messages.success(request, f"{campaign}: {run.message}") if (run.sent or run.finished) and not run.failed \
        else messages.error(request, f"{campaign}: {run.message}") if run.failed \
        else messages.warning(request, f"{campaign}: {run.message}")


@staff_required
def campaign_list(request):
    qs = Campaign.objects.all()
    qs = search(qs, request, ["name", "subject", "brief", "body"])
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    personalise = request.GET.get("personalise")
    if personalise in ("1", "0"):
        qs = qs.filter(personalise=(personalise == "1"))

    if request.method == "POST":
        action = request.POST.get("action")
        ids = request.POST.getlist("selected")
        selected = list(Campaign.objects.filter(pk__in=ids))
        if not ids:
            messages.warning(request, "Nothing selected.")
        elif action == "schedule":
            for c in selected:
                _do_schedule(request, c)
        elif action == "send_now":
            for c in selected:
                _do_send_now(request, c)
        elif action == "cancel":
            n = Campaign.objects.filter(pk__in=ids).exclude(status=Campaign.Status.SENT).update(
                status=Campaign.Status.CANCELLED, updated_at=timezone.now())
            messages.success(request, f"Cancelled {n}.")
        elif action == "back_to_draft":
            n = Campaign.objects.filter(pk__in=ids).exclude(status=Campaign.Status.SENDING).update(
                status=Campaign.Status.DRAFT, updated_at=timezone.now())
            messages.success(request, f"Moved {n} back to draft.")
        elif action == "retry_failed":
            for c in selected:
                n = c.deliveries.filter(
                    status__in=[CampaignDelivery.Status.FAILED, CampaignDelivery.Status.SENDING]
                ).update(status=CampaignDelivery.Status.PENDING, error="")
                if n:
                    c.status = Campaign.Status.SCHEDULED
                    c.save(update_fields=["status", "updated_at"])
                messages.success(request, f"{c}: {n} delivery{'' if n == 1 else 'ies'} queued again."
                                 + (" The scheduler picks them up shortly." if n else ""))
        return redirect(f"{request.path}?{request.GET.urlencode()}")

    page_obj = paginate(request, qs.order_by("-created_at").prefetch_related("audience_tags"))
    rows = list(page_obj)
    progress = _progress_map(rows)
    sizes = _audience_sizes(rows)
    for c in rows:
        c.audience_size = sizes[c.pk]
        c.progress_report = progress[c.pk]

    filter_groups = [
        {"title": "Status", "param": "status", "options": filter_options(request, "status", Campaign.Status.choices)},
        {"title": "Personalise", "param": "personalise", "options": filter_options(request, "personalise", [("1", "Yes"), ("0", "No")])},
    ]
    context = {
        "page_title": "Campaigns",
        "page_blurb": "One-off emails on any subject - to everyone or a slice of readers, "
                      "now or at a set time.",
        "breadcrumbs": [("Campaigns", None)],
        "page_obj": page_obj,
        "result_count": qs.count(),
        "search_placeholder": "Search campaigns…",
        "filter_groups": filter_groups,
        "has_active_filters": any(request.GET.get(g["param"]) for g in filter_groups),
        "bulk_actions": BULK_ACTIONS,
        "delete_kind": "campaigns",
        "add_url": reverse("desk:campaigns_add"),
        "idea_url": reverse("desk:campaigns_from_idea"),
    }
    return render(request, "desk/campaign_list.html", context)


@staff_required
def campaign_from_idea(request):
    """Type what the email is about; AI drafts everything; you review.

    The draft is a real Campaign row from the moment it exists, so the
    editor lands on the ordinary edit form with every field filled in and
    the audience already set - and can change any of it before Preview or
    Send. Nothing here sends.
    """
    from opportunities.models import Category, Tag
    from recommendations import ai

    if request.method != "POST":
        return render(request, "desk/campaign_idea.html", {
            "page_title": "New campaign",
            "breadcrumbs": [("Campaigns", reverse("desk:campaigns_list")), ("New", None)],
            "ai_on": ai.is_enabled("write_campaigns"),
        })

    idea = (request.POST.get("idea") or "").strip()
    if not idea:
        messages.warning(request, "Say what the email is about first.")
        return redirect("desk:campaigns_from_idea")

    draft = ai.draft_campaign(idea) if ai.is_enabled("write_campaigns") else None
    if not draft:
        campaign = Campaign.objects.create(
            name=idea[:60], subject=idea[:120], brief=idea, created_by=request.user)
        messages.warning(request, "AI didn't draft this one - the idea is saved as the "
                                  "brief. Fill in the subject and body, or try again once "
                                  "AI is working (Settings → Health).")
        return redirect("desk:campaigns_change", pk=campaign.pk)

    allowed = {v for v, _ in Category.choices}
    campaign = Campaign.objects.create(
        name=(draft.get("name") or idea[:60])[:120],
        subject=(draft.get("subject") or idea[:120])[:200],
        brief=draft.get("brief") or idea,
        body=draft.get("body") or "",
        link_label=(draft.get("link_label") or "")[:60] or "Book / learn more",
        audience_categories=[c for c in draft.get("categories") or [] if c in allowed],
        audience_location=(draft.get("location") or "")[:120],
        created_by=request.user,
    )
    campaign.audience_tags.set(Tag.objects.filter(slug__in=draft.get("tags") or []))
    reach = campaign.audience().count()
    messages.success(
        request,
        f"Drafted “{campaign.name}” for {reach} reader{'' if reach == 1 else 's'} "
        f"({campaign.audience_description()}). Read it, change anything, then Preview "
        "and Send. Nothing has gone out.")
    return redirect("desk:campaigns_change", pk=campaign.pk)


@staff_required
def campaign_form(request, pk=None):
    instance = get_object_or_404(Campaign, pk=pk) if pk else None
    if request.method == "POST":
        form = CampaignForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            if not obj.pk:
                obj.created_by = request.user
            obj.audience_categories = form.cleaned_data["audience_categories"]
            obj.save()
            form.save_m2m()
            messages.success(request, f"Saved “{obj.name}”.")
            if "save_add_another" in request.POST:
                return redirect("desk:campaigns_add")
            return redirect("desk:campaigns_change", pk=obj.pk)
    else:
        form = CampaignForm(instance=instance)

    context = {
        "page_title": "Add campaign" if not instance else instance.name,
        "breadcrumbs": [("Campaigns", reverse("desk:campaigns_list")),
                        ("Add" if not instance else instance.name, None)],
        "form": form,
        "instance": instance,
        "audience_count": instance.audience().count() if instance else None,
        "audience_description": instance.audience_description() if instance else None,
        "progress": _progress(instance) if instance else None,
        "deliveries": instance.deliveries.select_related("reader").order_by("pk")[:200] if instance else None,
        "other_events": (Opportunity.objects.exclude(status=Opportunity.Status.ARCHIVED)
                         .exclude(pk=instance.event_id).order_by("-created_at")[:100]
                         if instance else []),
    }
    return render(request, "desk/campaign_form.html", context)


def _sample_reader(campaign, request):
    wanted = request.GET.get("reader") or request.POST.get("reader")
    if wanted:
        found = campaign.audience().filter(pk=wanted).first()
        if found:
            return found
    first = campaign.audience().first()
    if first:
        return first
    return Reader(name="Ada Example", email="ada@example.com", location="London",
                  interest_categories=["music"])


@staff_required
def campaign_preview(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    reader = _sample_reader(campaign, request)
    try:
        preview = preview_for(campaign, reader, budget_seconds=12)
        error = None
    except Exception as exc:
        preview, error = {}, str(exc)
    context = {
        "page_title": f"Preview: {campaign.name}",
        "breadcrumbs": [("Campaigns", reverse("desk:campaigns_list")), (campaign.name, reverse("desk:campaigns_change", args=[pk])), ("Preview", None)],
        "campaign": campaign,
        "reader": reader,
        "preview": preview,
        "error": error,
        "readers": list(campaign.audience()[:50]),
    }
    return render(request, "desk/campaign_preview.html", context)


@staff_required
def campaign_test_send(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    campaign = get_object_or_404(Campaign, pk=pk)
    if not request.user.email:
        messages.error(request, "Your account has no email address - add one under Access → Users first.")
        return redirect("desk:campaigns_change", pk=pk)
    try:
        note = send_test(campaign, request.user.email, _sample_reader(campaign, request))
        messages.success(request, note)
    except Exception as exc:
        messages.error(request, f"Test send failed: {exc}")
    return redirect("desk:campaigns_change", pk=pk)


@staff_required
def campaign_schedule(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    _do_schedule(request, get_object_or_404(Campaign, pk=pk))
    return redirect("desk:campaigns_change", pk=pk)


@staff_required
def campaign_send_now(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    _do_send_now(request, get_object_or_404(Campaign, pk=pk))
    return redirect("desk:campaigns_change", pk=pk)


@staff_required
def campaign_suggest_audience(request, pk):
    """Let AI read the campaign and propose who it's for.

    Applied straight away rather than printed as advice: the audience is
    visible on the page, saved with the campaign, and trivially undone -
    unlike listing classification, where a wrong guess would be published.
    Nothing is sent by this.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    campaign = get_object_or_404(Campaign, pk=pk)

    from opportunities.models import Category, Tag
    from recommendations import ai

    if not ai.is_enabled("write_campaigns"):
        messages.warning(request, "AI is not configured, or campaign writing is switched "
                                  "off in Site configuration → AI assistance.")
        return redirect("desk:campaigns_change", pk=pk)

    suggestion = ai.suggest_audience(campaign)
    if not suggestion:
        messages.warning(request, "Could not suggest an audience - see the AI row on "
                                  "the Overview page for why.")
        return redirect("desk:campaigns_change", pk=pk)

    allowed = {v for v, _ in Category.choices}
    categories = [c for c in suggestion.get("categories") or [] if c in allowed]
    tags = list(Tag.objects.filter(slug__in=suggestion.get("tags") or []))

    campaign.audience_categories = categories
    campaign.audience_location = (suggestion.get("location") or "")[:120]
    campaign.save(update_fields=["audience_categories", "audience_location", "updated_at"])
    campaign.audience_tags.set(tags)

    reach = campaign.audience().count()
    messages.success(
        request,
        f"Audience set to {reach} reader{'' if reach == 1 else 's'}: "
        f"{', '.join(t.name for t in tags) or 'no interest restriction'}"
        f"{', ' + campaign.audience_location if campaign.audience_location else ''}. "
        f"{suggestion.get('reasoning', '')} Change it on the Audience tab if that's wrong.")
    return redirect("desk:campaigns_change", pk=pk)


@staff_required
def campaign_cancel(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    campaign = get_object_or_404(Campaign, pk=pk)
    campaign.status = Campaign.Status.CANCELLED
    campaign.save(update_fields=["status", "updated_at"])
    messages.success(request, f"{campaign}: cancelled. Anyone not yet emailed won't be.")
    return redirect("desk:campaigns_change", pk=pk)


@staff_required
def campaign_from_event(request, pk):
    """A campaign about one event, drafted from the event's own record."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    event = get_object_or_404(Opportunity, pk=pk)
    angle = (request.POST.get("angle") or "").strip()
    campaign = _draft_about(request, event, angle)
    return redirect("desk:campaigns_change", pk=campaign.pk)


@staff_required
def campaign_rerun(request, pk):
    """Run this campaign again for another event.

    What a template used to be for: the angle, the audience and the
    personalisation are the reusable part, and they carry over. The
    subject and body are about the new event, so they are written again
    from its facts. The original is left exactly as it was.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    like = get_object_or_404(Campaign, pk=pk)
    event = get_object_or_404(Opportunity, pk=request.POST.get("event"))
    if event.status == Opportunity.Status.ARCHIVED:
        messages.warning(request, f"“{event.title}” has ended - pick an event that is still on.")
        return redirect("desk:campaigns_change", pk=pk)
    campaign = _draft_about(request, event, like.angle, like=like)
    return redirect("desk:campaigns_change", pk=campaign.pk)


def _draft_about(request, event, angle, like=None):
    """A campaign about `event`, in the spirit of `like` if one is given.

    The event is the brief: title, description, where, when, price, link.
    AI writes the email and picks the audience from that and nothing else,
    so the facts in the email are the facts on the event. When `like` is an
    earlier campaign being run again, its angle, audience rules,
    personalisation and button label carry over; only the words are new.
    """
    from opportunities.models import Category, Tag
    from recommendations import ai

    facts = "\n".join(filter(None, [
        f"Event: {event.title}",
        f"What: {event.description}",
        f"Editor's note: {event.editorial_note}" if event.editorial_note else "",
        f"Where: {event.location_name or ''} {event.location_area or ''}".strip(),
        f"When: {event.start_date or 'ongoing'} to {event.end_date or 'no fixed end'}",
        f"Price: {event.price_display or event.get_price_tier_display()}",
        f"Booking: {event.booking_url}",
        f"Interests it suits: {', '.join(t.name for t in event.tags.all()) or 'none set'}",
        f"Angle for this campaign: {angle}" if angle else "",
    ]))

    draft = ai.draft_campaign(facts) if ai.is_enabled("write_campaigns") else None
    allowed = {v for v, _ in Category.choices}
    carried = {
        "angle": angle, "event": event, "link_url": event.booking_url,
        "created_by": request.user,
        "personalise": like.personalise if like else True,
    }
    if draft:
        campaign = Campaign.objects.create(
            name=(draft.get("name") or event.title)[:120],
            subject=(draft.get("subject") or event.title)[:200],
            brief=draft.get("brief") or facts, body=draft.get("body") or "",
            link_label=(like.link_label if like else draft.get("link_label") or "Book / learn more")[:60],
            audience_categories=(list(like.audience_categories) if like
                                 else [c for c in draft.get("categories") or [] if c in allowed]),
            audience_location=(like.audience_location if like
                               else (draft.get("location") or event.location_area or ""))[:120],
            **carried)
        if like:
            campaign.audience_tags.set(like.audience_tags.all())
        else:
            tags = list(Tag.objects.filter(slug__in=draft.get("tags") or [])) or list(event.tags.all())
            campaign.audience_tags.set(tags)
        reach = campaign.audience().count()
        messages.success(
            request,
            f"Drafted “{campaign.name}” about {event.title} for {reach} "
            f"reader{'' if reach == 1 else 's'}"
            + (f", with the audience and angle of “{like.name}”" if like else "")
            + ". Read it, change anything, then Preview and Send. Nothing has gone out.")
    else:
        campaign = Campaign.objects.create(
            name=event.title[:120], subject=event.title[:200], brief=facts,
            link_label=like.link_label if like else "Book / learn more",
            audience_location=(like.audience_location if like else event.location_area) or "",
            audience_categories=(list(like.audience_categories) if like
                                 else ([event.category] if event.category in allowed else [])),
            **carried)
        campaign.audience_tags.set(like.audience_tags.all() if like else event.tags.all())
        messages.warning(request, "AI didn't draft this one - the event's facts are saved as "
                                  "the brief. Fill in the body, or try again once AI is "
                                  "working (Settings → Health).")
    return campaign
