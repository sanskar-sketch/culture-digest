"""Building and sending one reader's newsletter issue.

Extracted so the scheduled command and the admin's "send now" action run
exactly the same path - a test send that exercised different code wouldn't
prove much about the real one.
"""

from __future__ import annotations

import dataclasses

from django.db import transaction
from django.utils import timezone

from . import matching
from .emailing import send_newsletter
from .models import NewsletterIssue, Recommendation

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


def send_issue_for_reader(
    reader,
    *,
    dry_run: bool = False,
    min_recommendations: int = DEFAULT_MIN_RECOMMENDATIONS,
) -> SendResult:
    """Match, build the issue, and send it.

    A dry run builds the issue and renders the email, then rolls back, so it
    has no persistent effect - in particular it must not put opportunities
    on cooldown for a send that never happened.
    """
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

    with transaction.atomic():
        issue = NewsletterIssue.objects.create(reader=reader)
        for match in matches:
            Recommendation.objects.create(
                issue=issue,
                opportunity=match.opportunity,
                rationale=matching.build_rationale(match, reader),
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
