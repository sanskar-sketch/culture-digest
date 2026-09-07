"""Rendering and sending the newsletter email via SendGrid.

Kept as a thin wrapper around the SendGrid SDK so the rest of the codebase
doesn't depend on it directly - swapping providers means changing this one
module. With SENDGRID_API_KEY unset, sending falls back to a dry run
(rendered and logged, never transmitted) so the app works without an
account.
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
    from siteconfig.models import SiteConfig

    config = SiteConfig.load()
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
        "site_config": config,
        "recommendations": rec_contexts,
        "unsubscribe_url": build_unsubscribe_url(reader),
    }

    first_name = reader.name.split(" ")[0] if reader.name else ""
    try:
        subject = config.subject_template.format(
            name=f"{first_name}, " if first_name else "", count=len(recs)
        )
    except (KeyError, IndexError, ValueError):
        # An editor can mistype a placeholder - a broken subject template
        # must not stop the newsletter going out.
        logger.warning("Bad subject_template %r - using the default",
                       config.subject_template)
        subject = f"{first_name + ', ' if first_name else ''}{len(recs)} things you'll probably love this week"

    html_body = render_to_string("emails/newsletter.html", context)
    text_body = render_to_string("emails/newsletter.txt", context)
    return subject, html_body, text_body


def send_newsletter(issue, dry_run: bool = False) -> str | None:
    """Render and send the newsletter for one issue. Returns the provider
    message id, or None if this was a dry run / sending is unconfigured."""
    subject, html_body, text_body = render_newsletter(issue)

    from siteconfig.models import SiteConfig

    if dry_run or not settings.SENDGRID_API_KEY:
        logger.info(
            "[dry-run] Would send newsletter to %s: %s", issue.reader.email, subject
        )
        return None

    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail

    message = Mail(
        from_email=SiteConfig.load().resolved_email_from,
        to_emails=issue.reader.email,
        subject=subject,
        plain_text_content=text_body,
        html_content=html_body,
    )
    response = SendGridAPIClient(settings.SENDGRID_API_KEY).send(message)

    # SendGrid signals failure by status code rather than by raising, so a
    # 4xx would otherwise look like a successful send and the issue would be
    # marked sent when nothing was delivered.
    if response.status_code >= 300:
        raise RuntimeError(
            f"SendGrid rejected the send with HTTP {response.status_code}: "
            f"{getattr(response, 'body', b'')!r}"
        )

    # The provider's id lives in a response header, not the body.
    headers = response.headers or {}
    return headers.get("X-Message-Id") or headers.get("x-message-id")
