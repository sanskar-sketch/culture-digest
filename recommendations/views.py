"""What a reader does with the email: says, and does.

Feedback records an opinion. A click records an action - which pick they
opened, and which part of the email they used. Both are attributed by an
unguessable token in the URL, so nothing identifying travels in a link.

The redirect targets come from the database, never from the query string.
A `?next=` an attacker could set would make this an open redirect on a
domain readers have been taught to trust.
"""

from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import LinkClick, Recommendation

ACTION_TO_FEEDBACK = {
    "more-like-this": Recommendation.Feedback.MORE_LIKE_THIS,
    "not-for-me": Recommendation.Feedback.NOT_FOR_ME,
    "save": Recommendation.Feedback.SAVE,
    "booked": Recommendation.Feedback.BOOKED,
}


def _record(reader, url, section, recommendation=None, campaign=None):
    """Log a click. Never let logging cost the reader their redirect."""
    try:
        LinkClick.objects.create(reader=reader, url=url[:2000], section=section,
                                 recommendation=recommendation, campaign=campaign)
    except Exception:  # pragma: no cover - defensive
        pass


def feedback_view(request, token, action):
    feedback_value = ACTION_TO_FEEDBACK.get(action)
    if feedback_value is None:
        raise Http404("Unknown feedback action")

    recommendation = get_object_or_404(Recommendation, feedback_token=token)
    recommendation.feedback = feedback_value
    recommendation.feedback_at = timezone.now()
    recommendation.save(update_fields=["feedback", "feedback_at"])
    _record(recommendation.issue.reader, request.path, LinkClick.Section.FEEDBACK,
            recommendation=recommendation)

    return render(
        request,
        "feedback/recorded.html",
        {"recommendation": recommendation, "feedback_label": recommendation.get_feedback_display()},
    )


def booking_click_view(request, token):
    """Record that a reader opened a pick, then send them on to it."""
    recommendation = get_object_or_404(
        Recommendation.objects.select_related("opportunity", "issue__reader"),
        feedback_token=token)
    destination = recommendation.opportunity.booking_url
    if not destination:
        raise Http404("This listing has no link.")
    _record(recommendation.issue.reader, destination, LinkClick.Section.BOOKING,
            recommendation=recommendation)
    return redirect(destination)


def campaign_click_view(request, token):
    """The button under a campaign email."""
    from campaigns.models import CampaignDelivery

    delivery = get_object_or_404(
        CampaignDelivery.objects.select_related("campaign", "reader"), token=token)
    destination = delivery.campaign.link_url
    if not destination:
        raise Http404("This campaign has no link.")
    _record(delivery.reader, destination, LinkClick.Section.CAMPAIGN,
            campaign=delivery.campaign)
    return redirect(destination)
