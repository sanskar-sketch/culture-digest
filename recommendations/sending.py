"""Building and sending one reader's newsletter issue.

Extracted so the scheduled command and the admin's "send now" action run
exactly the same path - a test send that exercised different code wouldn't
prove much about the real one.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import ai, compose, matching
from .emailing import send_newsletter
from .models import PICK_ORDER, NewsletterIssue, Recommendation

logger = logging.getLogger(__name__)

# Below this many strong matches, a reader is skipped rather than sent a
# padded-out issue. Quality over volume is the whole premise - but one
# genuinely good match is still quality, and while the catalogue is small
# it is often all there is. The editable setting overrides this.
DEFAULT_MIN_RECOMMENDATIONS = 1


@dataclasses.dataclass
class SendResult:
    reader_email: str
    sent: bool
    match_count: int
    message: str
    issue: NewsletterIssue | None = None


def send_scheduled_newsletter(*, budget_seconds: float, dry_run: bool = False) -> dict:
    """One resumable pass of the cadence send.

    Readers who already have an issue from today are skipped, so passes can
    be short and frequent; when a pass reaches the end of the list the day
    is marked sent and later passes stop at `should_send_now`.
    """
    from readers.models import Reader
    from siteconfig.models import SiteConfig

    config = SiteConfig.load()
    should, why = config.should_send_now()
    report = {"ran": False, "why": why, "sent": 0, "skipped": 0, "failed": 0,
              "remaining": 0, "done": False}
    if not should:
        return report
    report["ran"] = True

    today = timezone.localdate()
    already = NewsletterIssue.objects.filter(created_at__date=today).values_list(
        "reader_id", flat=True)
    readers = list(Reader.objects.filter(is_active=True).exclude(pk__in=already).order_by("pk"))
    if dry_run:
        report["remaining"] = len(readers)
        return report

    budget = ai.TimeBudget(budget_seconds)
    processed = 0
    for reader in readers:
        if budget.exhausted():
            break
        try:
            result = send_issue_for_reader(reader)
            report["sent" if result.sent else "skipped"] += 1
        except Exception:
            logger.exception("Scheduled newsletter to %s failed", reader.email)
            report["failed"] += 1
        processed += 1

    report["remaining"] = len(readers) - processed
    if report["remaining"] == 0:
        config.last_sent_on = today
        config.save(update_fields=["last_sent_on", "updated_at"])
        report["done"] = True
    return report


def send_issue_for_reader(
    reader,
    *,
    dry_run: bool = False,
    min_recommendations: int | None = None,
    pool=None,
    overrides=None,
    composed=None,
) -> SendResult:
    """Compose the reader's culture week, store it and send it.

    `composed` is an issue already written - a draft the editor read and
    perhaps changed - and is sent exactly as it stands, without asking AI
    again. Otherwise it is composed here.

    A dry run builds the issue and renders the email, then rolls back, so it
    has no persistent effect - in particular it must not put opportunities
    on cooldown for a send that never happened.
    """
    from siteconfig.models import SiteConfig

    config = SiteConfig.load()
    if min_recommendations is None:
        min_recommendations = config.min_recommendations

    if composed is None:
        composed = compose.compose(reader, pool=pool, overrides=overrides)
    count = len(composed.picks)
    if count < min_recommendations:
        return SendResult(
            reader_email=reader.email,
            sent=False,
            match_count=count,
            message=(
                f"Skipped: only {count} strong match"
                f"{'' if count == 1 else 'es'} "
                f"(needs {min_recommendations}). " + _why_thin(reader, pool)
            ),
        )

    with transaction.atomic():
        issue = compose.materialise(composed)
        message_id = send_newsletter(issue, dry_run=dry_run)

        if dry_run:
            transaction.set_rollback(True)
            return SendResult(
                reader_email=reader.email,
                sent=False,
                match_count=count,
                message=f"Dry run: would send {count} recommendations.",
            )

        issue.sent_at = timezone.now()
        issue.provider_message_id = message_id or ""
        issue.save(update_fields=["sent_at", "provider_message_id"])

    return SendResult(
        reader_email=reader.email,
        sent=True,
        match_count=count,
        message=f"Sent {count} recommendations"
                + (f" (message id {message_id})." if message_id else "."),
        issue=issue,
    )


def _why_thin(reader, pool=None) -> str:
    """Why this reader has too little to send, in the words of the real cause.

    "Add more events" is the right advice only sometimes. Just as often
    everything that suits them went out recently and the cooldown is
    holding it back, or they never told us what they like. Saying the
    wrong one sends an editor off to fix something that isn't broken.
    """
    from opportunities.models import Opportunity
    from siteconfig.models import SiteConfig

    today = timezone.localdate()
    live = Opportunity.objects.filter(status=Opportunity.Status.PUBLISHED).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today))
    if pool is not None:
        live = live.filter(id__in=list(pool))
    if not live.exists():
        return ("Nothing is published and still on"
                + (" among the events you ticked." if pool is not None else "."))

    config = SiteConfig.load()
    cutoff = timezone.now() - timedelta(days=config.cooldown_days)
    on_cooldown = Recommendation.objects.filter(
        issue__reader=reader, created_at__gte=cutoff,
        opportunity__in=live).values("opportunity_id").distinct().count()
    if on_cooldown >= live.count():
        return (f"Everything that suits them went out in the last "
                f"{config.cooldown_days} days, so the cooldown is holding it back. "
                "Add events, or wait.")
    if not reader.interest_tags.exists() and not reader.ai_inferred_tags.exists():
        return "They have no interests recorded yet, so nothing can score for them."
    return ("Nothing published is close enough on their interests, area, budget "
            "and dates. Add events, or widen theirs.")


def preview_issue_for_reader(reader, min_recommendations: int | None = None,
                             pool=None, overrides=None, composed=None) -> dict:
    """What this reader's next culture week would look like, rendered.

    The same composition a send uses, rendered without storing anything:
    nothing goes on cooldown, and the links go straight to the venues
    because the picks have no records yet.
    """
    from siteconfig.models import SiteConfig

    from .emailing import render_composed

    config = SiteConfig.load()
    if min_recommendations is None:
        min_recommendations = config.min_recommendations

    try:
        if composed is None:
            composed = compose.compose(reader, pool=pool, overrides=overrides)
        count = len(composed.picks)
        if count < min_recommendations:
            return {
                "ok": False, "match_count": count,
                "message": (f"Skipped: only {count} strong match"
                            f"{'' if count == 1 else 'es'} "
                            f"(needs {min_recommendations}). " + _why_thin(reader, pool)),
            }
        subject, html, text = render_composed(composed, tracked=False)
    except Exception as exc:
        logger.exception("Preview failed for %s", reader.email)
        return {"ok": False, "match_count": 0, "message": f"Could not build a preview: {exc}"}

    return {
        "ok": True, "match_count": count, "subject": subject, "html": html, "text": text,
        "composed": composed,
        "intro": composed.intro, "closing": composed.closing,
        "picks": [
            {"title": p.opportunity.title, "score": p.score, "event_id": p.event_id,
             "rationale": p.rationale, "verdict": p.hook, "caveat": p.caveat,
             "stars": p.stars, "section": p.section, "is_top": p.is_top,
             "timing_label": p.timing_label, "edited": p.edited}
            for p in composed.picks
        ],
        "message": f"Would send {count} recommendations.",
    }
