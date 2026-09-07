"""Working through a campaign's deliveries within a time budget.

Runs from two places with the same code: the admin's "Send now" (inside a
web request, so it gets a short budget and hands the remainder to the
scheduler) and the `run_scheduled` command (a long budget). Every reader
has a delivery row; a run claims pending rows one at a time so two runs
overlapping - the cron firing while an editor presses Send now - cannot
email the same person twice.
"""

from __future__ import annotations

import dataclasses
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from recommendations import ai
from recommendations.emailing import _paragraphs, build_unsubscribe_url, deliver

from .composing import compose
from .models import Campaign, CampaignDelivery

logger = logging.getLogger(__name__)

# Inside a web request the whole send must finish under gunicorn's timeout.
WEB_BUDGET_SECONDS = 20.0


@dataclasses.dataclass
class CampaignRun:
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    remaining: int = 0
    finished: bool = False
    note: str = ""

    @property
    def message(self) -> str:
        if self.note:
            return self.note
        bits = [f"{self.sent} sent"]
        if self.failed:
            bits.append(f"{self.failed} failed")
        if self.skipped:
            bits.append(f"{self.skipped} skipped")
        text = ", ".join(bits) + "."
        if self.remaining:
            text += (f" {self.remaining} still to go - the scheduler carries on "
                     "within a few minutes.")
        elif self.finished:
            text += " Done."
        return text


def render_campaign(campaign, reader, subject: str, body_text: str) -> tuple[str, str, str]:
    """(subject, html, text) for one reader, via the editor's template if
    one is selected, else the built-in one."""
    from siteconfig.emails import EmailTemplate, render_email
    from siteconfig.models import SiteConfig

    first_name = (getattr(reader, "name", "") or "").split(" ")[0]
    context = {
        "reader": reader,
        "first_name": first_name,
        "campaign": campaign,
        "site_config": SiteConfig.load(),
        "body_text": body_text,
        "body_html": _paragraphs(body_text),
        "unsubscribe_url": build_unsubscribe_url(reader),
    }
    html, text, subject_override = render_email(
        EmailTemplate.Kind.CAMPAIGN, context,
        ("emails/campaign.html", "emails/campaign.txt"),
    )
    return subject_override or subject, html, text


def preview_for(campaign, reader, budget_seconds: float | None = None):
    """What one reader would get, without touching the database or sending."""
    budget = ai.TimeBudget(budget_seconds) if budget_seconds else None
    subject, body_text, personalised = compose(campaign, reader, budget)
    subject, html, text = render_campaign(campaign, reader, subject, body_text)
    return {"subject": subject, "html": html, "text": text, "body_text": body_text,
            "personalised": personalised}


def _ensure_deliveries(campaign) -> int:
    """A row per audience member, created once. Returns how many were added.

    The audience is resolved when sending starts, not when the campaign is
    written - someone who signs up in between still gets it.
    """
    existing = set(campaign.deliveries.values_list("reader_id", flat=True))
    new = [
        CampaignDelivery(campaign=campaign, reader=reader)
        for reader in campaign.audience() if reader.pk not in existing
    ]
    CampaignDelivery.objects.bulk_create(new)
    return len(new)


def _claim(delivery) -> bool:
    """Take a pending delivery for this run. False if another run got it."""
    with transaction.atomic():
        return bool(
            CampaignDelivery.objects.filter(
                pk=delivery.pk, status=CampaignDelivery.Status.PENDING
            ).update(status=CampaignDelivery.Status.SENDING)
        )


def _send_one(campaign, delivery, budget, run: CampaignRun) -> None:
    reader = delivery.reader
    try:
        subject, body_text, personalised = compose(campaign, reader, budget)
        subject, html, text = render_campaign(campaign, reader, subject, body_text)
        delivery.subject = subject
        delivery.body_text = body_text
        delivery.personalised = personalised

        if not settings.SENDGRID_API_KEY:
            # Recording these as sent would be a lie an editor could act on.
            delivery.status = CampaignDelivery.Status.SKIPPED
            delivery.error = "No SENDGRID_API_KEY is set, so nothing was sent."
            run.skipped += 1
        else:
            message_id = deliver(reader.email, subject, text, html)
            delivery.status = CampaignDelivery.Status.SENT
            delivery.sent_at = timezone.now()
            delivery.provider_message_id = message_id or ""
            run.sent += 1
    except Exception as exc:
        logger.exception("Campaign %s: delivery to %s failed", campaign.pk, reader.email)
        delivery.status = CampaignDelivery.Status.FAILED
        delivery.error = str(exc)[:2000]
        campaign.last_error = f"{reader.email}: {exc}"[:2000]
        run.failed += 1
    delivery.save()
    run.attempted += 1


def send_campaign(campaign: Campaign, *, budget_seconds: float) -> CampaignRun:
    """Send as much of the campaign as fits in the budget.

    Only a scheduled or in-progress campaign is touched; anything else is
    left alone so a stray call can never send a draft.
    """
    run = CampaignRun()
    if not campaign.is_sendable:
        run.note = f"Not sent: the campaign is {campaign.get_status_display().lower()}."
        return run

    campaign.status = Campaign.Status.SENDING
    campaign.started_at = campaign.started_at or timezone.now()
    campaign.save(update_fields=["status", "started_at", "updated_at"])

    _ensure_deliveries(campaign)
    budget = ai.TimeBudget(budget_seconds)

    pending = (
        campaign.deliveries.filter(status=CampaignDelivery.Status.PENDING)
        .select_related("reader").order_by("pk")
    )
    for delivery in list(pending):
        if budget.exhausted():
            break
        if not _claim(delivery):
            continue
        _send_one(campaign, delivery, budget, run)

    run.remaining = campaign.deliveries.filter(
        status=CampaignDelivery.Status.PENDING).count()
    if run.remaining == 0:
        campaign.status = Campaign.Status.SENT
        campaign.finished_at = timezone.now()
        run.finished = True
    campaign.save(update_fields=["status", "finished_at", "last_error", "updated_at"])
    return run


def send_test(campaign, to_email: str, sample_reader, budget_seconds: float = 12.0) -> str:
    """Email one rendered version to an editor. Returns a message for them."""
    preview = preview_for(campaign, sample_reader, budget_seconds)
    if not settings.SENDGRID_API_KEY:
        return "No SENDGRID_API_KEY is set, so nothing was sent - use Preview instead."
    deliver(to_email, f"[Test] {preview['subject']}", preview["text"], preview["html"])
    who = getattr(sample_reader, "email", "a stand-in reader")
    return (f"Sent a test to {to_email}, written as it would be for {who}"
            + (" (AI-personalised)." if preview["personalised"] else " (body as written)."))
