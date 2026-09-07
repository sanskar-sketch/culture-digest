from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from readers.models import Reader
from recommendations import matching
from recommendations.emailing import send_newsletter
from recommendations.models import NewsletterIssue, Recommendation


class Command(BaseCommand):
    help = (
        "Match each active reader against published opportunities and send "
        "them a personalised newsletter. Skips readers with no strong "
        "matches rather than sending a weak/empty issue."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute matches and render emails, but don't actually send them.",
        )
        parser.add_argument(
            "--reader",
            help="Only send to the reader with this email address (for testing).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Only process the first N active readers.",
        )
        parser.add_argument(
            "--min-recommendations",
            type=int,
            default=2,
            help="Skip a reader if fewer than this many matches were found.",
        )

    def handle(self, *args, **options):
        readers = Reader.objects.filter(is_active=True).order_by("id")
        if options["reader"]:
            readers = readers.filter(email=options["reader"].strip().lower())
        if options["limit"]:
            readers = readers[: options["limit"]]

        sent, skipped = 0, 0

        for reader in readers:
            matches = matching.top_matches_for_reader(reader)
            if len(matches) < options["min_recommendations"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipping {reader.email}: only {len(matches)} strong match(es)."
                    )
                )
                skipped += 1
                continue

            # Dry runs build the issue/recommendations only long enough to
            # render the email, then roll the transaction back - a dry run
            # must have zero persistent effect (in particular it must not
            # count towards the recommendation cooldown).
            with transaction.atomic():
                issue = NewsletterIssue.objects.create(reader=reader)
                for match in matches:
                    Recommendation.objects.create(
                        issue=issue,
                        opportunity=match.opportunity,
                        rationale=matching.build_rationale(match, reader),
                        score=match.score,
                    )

                message_id = send_newsletter(issue, dry_run=options["dry_run"])

                if options["dry_run"]:
                    transaction.set_rollback(True)
                else:
                    issue.sent_at = timezone.now()
                    issue.resend_message_id = message_id or ""
                    issue.save(update_fields=["sent_at", "resend_message_id"])

            sent += 1
            verb = "Would send" if options["dry_run"] else "Sent"
            self.stdout.write(
                self.style.SUCCESS(f"{verb} {len(matches)} recommendation(s) to {reader.email}")
            )

        self.stdout.write(self.style.SUCCESS(f"Done. Sent: {sent}, skipped: {skipped}."))
