"""Building and sending one reader's newsletter issue.

Extracted so the scheduled command and the admin's "send now" action run
exactly the same path - a test send that exercised different code wouldn't
prove much about the real one.
"""

from __future__ import annotations

import dataclasses
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import ai, matching
from .emailing import send_newsletter
from .models import NewsletterIssue, Recommendation

logger = logging.getLogger(__name__)

# Below this many strong matches, a reader is skipped rather than sent a
# padded-out issue. Quality over volume is the whole premise.
DEFAULT_MIN_RECOMMENDATIONS = 2


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
) -> SendResult:
    """Match, build the issue, and send it.

    A dry run builds the issue and renders the email, then rolls back, so it
    has no persistent effect - in particular it must not put opportunities
    on cooldown for a send that never happened.
    """
    from siteconfig.models import SiteConfig

    config = SiteConfig.load()
    if min_recommendations is None:
        min_recommendations = config.min_recommendations

    matches = matching.top_matches_for_reader(reader)
    if len(matches) < min_recommendations:
        return SendResult(
            reader_email=reader.email,
            sent=False,
            match_count=len(matches),
            message=(
                f"Skipped: only {len(matches)} strong match"
                f"{'' if len(matches) == 1 else 'es'} "
                f"(needs {min_recommendations}). Add more published opportunities."
            ),
        )

    # One AI call per recommendation adds up; cap the total so a send can
    # never outlive the request that triggered it.
    budget = ai.TimeBudget(config.ai_send_budget_seconds)

    with transaction.atomic():
        issue = NewsletterIssue.objects.create(reader=reader)
        for match in matches:
            rationale, verdict = matching.build_rationale(match, reader, budget=budget)
            Recommendation.objects.create(
                issue=issue,
                opportunity=match.opportunity,
                rationale=rationale,
                verdict=verdict,
                score=match.score,
            )

        message_id = send_newsletter(issue, dry_run=dry_run)

        if dry_run:
            transaction.set_rollback(True)
            return SendResult(
                reader_email=reader.email,
                sent=False,
                match_count=len(matches),
                message=f"Dry run: would send {len(matches)} recommendations.",
            )

        issue.sent_at = timezone.now()
        issue.provider_message_id = message_id or ""
        issue.save(update_fields=["sent_at", "provider_message_id"])

    return SendResult(
        reader_email=reader.email,
        sent=True,
        match_count=len(matches),
        message=f"Sent {len(matches)} recommendations"
                + (f" (message id {message_id})." if message_id else "."),
        issue=issue,
    )
