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

from .models import LinkClick, ReaderReply, Recommendation

# Long enough for a proper paragraph, short enough that nobody pastes a book.
REPLY_MAX = 4000

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


def review_click_view(request, token, review_id):
    """A critic review linked from a pick: record it, then open the review."""
    recommendation = get_object_or_404(
        Recommendation.objects.select_related("issue__reader"), feedback_token=token)
    # The review has to belong to this pick's event, or the id in the path
    # could be used to bounce readers to any stored URL.
    review = get_object_or_404(recommendation.opportunity.reviews, pk=review_id)
    _record(recommendation.issue.reader, review.url, LinkClick.Section.REVIEW,
            recommendation=recommendation)
    return redirect(review.url)


def reply_view(request, token):
    """A reader telling us, in words, what they thought.

    Reached from "Tell me why" under a pick, from the feedback page, or
    from the foot of the email about the week as a whole. What they write
    is kept, read into their taste, and handed to whoever writes their next
    issue - which is how "tribute nights aren't for me" changes every week
    after it.
    """
    recommendation = get_object_or_404(
        Recommendation.objects.select_related("opportunity", "issue__reader"),
        feedback_token=token)
    reader = recommendation.issue.reader
    about_week = (request.POST.get("about") or request.GET.get("about")) == "week"

    if request.method == "POST":
        text = (request.POST.get("text") or "").strip()[:REPLY_MAX]
        if text:
            ReaderReply.objects.create(
                reader=reader, issue=recommendation.issue,
                recommendation=None if about_week else recommendation, text=text)
            from readers import interests

            interests.reread(reader)
            return render(request, "feedback/replied.html",
                          {"recommendation": recommendation, "about_week": about_week})

    return render(request, "feedback/reply.html",
                  {"recommendation": recommendation, "about_week": about_week})


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
