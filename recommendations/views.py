from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Recommendation

ACTION_TO_FEEDBACK = {
    "more-like-this": Recommendation.Feedback.MORE_LIKE_THIS,
    "not-for-me": Recommendation.Feedback.NOT_FOR_ME,
    "save": Recommendation.Feedback.SAVE,
    "booked": Recommendation.Feedback.BOOKED,
}


def feedback_view(request, token, action):
    feedback_value = ACTION_TO_FEEDBACK.get(action)
    if feedback_value is None:
        raise Http404("Unknown feedback action")

    recommendation = get_object_or_404(Recommendation, feedback_token=token)
    recommendation.feedback = feedback_value
    recommendation.feedback_at = timezone.now()
    recommendation.save(update_fields=["feedback", "feedback_at"])

    return render(
        request,
        "feedback/recorded.html",
        {"recommendation": recommendation, "feedback_label": recommendation.get_feedback_display()},
    )
