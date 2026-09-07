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



def _paragraphs(text: str):
    """The editor writes in paragraphs; email HTML needs them marked up."""
    from django.utils.html import escape, mark_safe

    blocks = [b.strip() for b in (text or "").split("\n") if b.strip()]
    return mark_safe("".join(
        f'<p style="margin:0 0 12px;">{escape(b)}</p>' for b in blocks
    ))


def _date_range(opportunity) -> str:
    """'Mon 7 - Sat 12 Sep', or a single date, or nothing."""
    start, end = opportunity.start_date, opportunity.end_date
    if not start and not end:
        return ""
    if start and end and start != end:
        if (start.year, start.month) == (end.year, end.month):
            return f"{start:%a %-d}\u2013{end:%a %-d %b}"
        return f"{start:%-d %b}\u2013{end:%-d %b}"
    return f"{(start or end):%a %-d %b}"


def _issue_dates(recs) -> str:
    """The week this issue covers, from the picks themselves."""
    from django.utils import timezone

    today = timezone.localdate()
    dates = [r.opportunity.start_date for r in recs if r.opportunity.start_date]
    if not dates:
        return f"{today:%-d %B %Y}"
    first, last = min(dates), max(dates)
    if first == last:
        return f"{first:%-d %B %Y}"
    if (first.year, first.month) == (last.year, last.month):
        return f"{first:%-d}\u2013{last:%-d %B %Y}"
    return f"{first:%-d %b}\u2013{last:%-d %b %Y}"


def profile_summary(reader) -> list[tuple[str, str]]:
    """The answers we actually have, for showing back to a new reader.

    Only what they filled in - listing blanks would read as a reproach for
    skipping optional questions.
    """
    rows = []
    if reader.interest_categories:
        rows.append(("Following", ", ".join(reader.interest_categories)))
    tags = list(reader.interest_tags.values_list("name", flat=True))
    if tags:
        rows.append(("Interests", ", ".join(tags)))
    if reader.location:
        rows.append(("Based in", reader.location))
    if reader.budget:
        rows.append(("Budget", reader.get_budget_display()))
    if reader.travel_radius:
        rows.append(("Will travel", reader.get_travel_radius_display()))
    if reader.availability:
        rows.append(("Free", ", ".join(reader.availability)))
    if reader.open_to_surprise:
        rows.append(("Wildcards", "Yes - surprise me sometimes"))
    return rows


def send_welcome(reader) -> str | None:
    """Confirm a new signup. Never allowed to break the signup itself.

    This runs inside the web request, so a provider that is slow or down
    must not cost someone their subscription - they filled in the form and
    we saved it, which is the part that matters.
    """
    from siteconfig.models import SiteConfig

    config = SiteConfig.load()
    if not config.send_welcome_email:
        return None

    context = {
        "reader": reader,
        "site_config": config,
        "summary": profile_summary(reader),
        "unsubscribe_url": build_unsubscribe_url(reader),
    }
    subject = config.welcome_subject.replace("{site}", config.site_name)

    from siteconfig.emails import EmailTemplate, render_email

    html_body, text_body, subject_override = render_email(
        EmailTemplate.Kind.WELCOME, context,
        ("emails/welcome.html", "emails/welcome.txt"),
    )
    subject = subject_override or subject

    if not settings.SENDGRID_API_KEY:
        logger.info("[dry-run] Would send welcome email to %s", reader.email)
        return None

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=config.resolved_email_from,
            to_emails=reader.email,
            subject=subject,
            plain_text_content=text_body,
            html_content=html_body,
        )
        response = SendGridAPIClient(settings.SENDGRID_API_KEY).send(message)
        if response.status_code >= 300:
            logger.error("Welcome email rejected for %s: HTTP %s %r",
                         reader.email, response.status_code, getattr(response, "body", b""))
            return None
        headers = response.headers or {}
        message_id = headers.get("X-Message-Id") or headers.get("x-message-id")
        logger.info("Welcome email accepted for %s (message id %s)", reader.email, message_id)
        return message_id
    except Exception:
        logger.exception("Welcome email failed for %s", reader.email)
        return None


def deliver(to_email: str, subject: str, text_body: str, html_body: str) -> str | None:
    """Send one email. Returns the provider's message id.

    Returns None without sending when no provider key is configured - the
    caller decides what that means for its records. Raises RuntimeError if
    the provider rejects the message, because SendGrid signals failure by
    status code rather than by raising.
    """
    from siteconfig.models import SiteConfig

    if not settings.SENDGRID_API_KEY:
        logger.info("[dry-run] Would send to %s: %s", to_email, subject)
        return None

    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail

    message = Mail(
        from_email=SiteConfig.load().resolved_email_from,
        to_emails=to_email,
        subject=subject,
        plain_text_content=text_body,
        html_content=html_body,
    )
    response = SendGridAPIClient(settings.SENDGRID_API_KEY).send(message)
    if response.status_code >= 300:
        raise RuntimeError(
            f"SendGrid rejected the send with HTTP {response.status_code}: "
            f"{getattr(response, 'body', b'')!r}"
        )
    headers = response.headers or {}
    return headers.get("X-Message-Id") or headers.get("x-message-id")


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
                "rationale_html": _paragraphs(rec.rationale),
                "verdict": rec.verdict,
                "fit_display": rec.fit_display,
                "dates": _date_range(rec.opportunity),
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
        "issue_dates": _issue_dates(recs),
        "recommendations": rec_contexts,
        "unsubscribe_url": build_unsubscribe_url(reader),
    }

    first_name = reader.name.split(" ")[0] if reader.name else ""
    try:
        subject = config.subject_template.format(
            name=f"{first_name}, " if first_name else "",
            first_name=first_name,
            count=len(recs),
            plural="" if len(recs) == 1 else "s",
        )
    except (KeyError, IndexError, ValueError):
        # An editor can mistype a placeholder - a broken subject template
        # must not stop the newsletter going out.
        logger.warning("Bad subject_template %r - using the default",
                       config.subject_template)
        subject = (f"{first_name + ', ' if first_name else ''}{len(recs)} "
                   f"thing{'' if len(recs) == 1 else 's'} worth your time")

    from siteconfig.emails import EmailTemplate, render_email

    html_body, text_body, subject_override = render_email(
        EmailTemplate.Kind.NEWSLETTER, context,
        ("emails/newsletter.html", "emails/newsletter.txt"),
    )
    return subject_override or subject, html_body, text_body


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
