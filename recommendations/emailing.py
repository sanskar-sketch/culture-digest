"""Rendering and sending the newsletter email via Resend
(https://resend.com).

Kept as a thin wrapper around the Resend SDK so the rest of the codebase
doesn't depend on it directly - swapping providers later means changing
this one module.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)


def build_feedback_url(token, action: str) -> str:
    path = reverse("recommendations:feedback", kwargs={"token": token, "action": action})
    return settings.SITE_BASE_URL.rstrip("/") + path


def build_unsubscribe_url(reader) -> str:
    path = reverse("readers:unsubscribe", kwargs={"token": reader.unsubscribe_token})
    return settings.SITE_BASE_URL.rstrip("/") + path


def render_newsletter(issue) -> tuple[str, str, str]:
    """Return (subject, html_body, text_body) for a NewsletterIssue."""
    reader = issue.reader
    recs = list(issue.recommendations.select_related("opportunity").all())

    rec_contexts = []
    for rec in recs:
        rec_contexts.append(
            {
                "opportunity": rec.opportunity,
                "rationale": rec.rationale,
                "booking_url": rec.opportunity.booking_url,
                "more_like_this_url": build_feedback_url(rec.feedback_token, "more-like-this"),
                "not_for_me_url": build_feedback_url(rec.feedback_token, "not-for-me"),
                "save_url": build_feedback_url(rec.feedback_token, "save"),
                "booked_url": build_feedback_url(rec.feedback_token, "booked"),
            }
        )

    context = {
        "reader": reader,
        "recommendations": rec_contexts,
        "unsubscribe_url": build_unsubscribe_url(reader),
    }

    first_name = reader.name.split(" ")[0] if reader.name else ""
    subject = f"{first_name + ', ' if first_name else ''}{len(recs)} things you'll probably love this week"

    html_body = render_to_string("emails/newsletter.html", context)
    text_body = render_to_string("emails/newsletter.txt", context)
    return subject, html_body, text_body


def send_newsletter(issue, dry_run: bool = False) -> str | None:
    """Render and send the newsletter for one issue. Returns the provider
    message id, or None if this was a dry run / sending is unconfigured."""
    subject, html_body, text_body = render_newsletter(issue)

    if dry_run or not settings.RESEND_API_KEY:
        logger.info(
            "[dry-run] Would send newsletter to %s: %s", issue.reader.email, subject
        )
        return None

    import resend

    resend.api_key = settings.RESEND_API_KEY
    response = resend.Emails.send(
        {
            "from": settings.NEWSLETTER_FROM_EMAIL,
            "to": [issue.reader.email],
            "subject": subject,
            "html": html_body,
            "text": text_body,
        }
    )
    return response.get("id") if isinstance(response, dict) else getattr(response, "id", None)
