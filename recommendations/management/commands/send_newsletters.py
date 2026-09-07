from django.core.management.base import BaseCommand

from readers.models import Reader
from recommendations.sending import DEFAULT_MIN_RECOMMENDATIONS, send_issue_for_reader


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
            default=DEFAULT_MIN_RECOMMENDATIONS,
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
            result = send_issue_for_reader(
                reader,
                dry_run=options["dry_run"],
                min_recommendations=options["min_recommendations"],
            )
            if result.sent or options["dry_run"]:
                sent += 1
                self.stdout.write(self.style.SUCCESS(f"{reader.email}: {result.message}"))
            else:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"{reader.email}: {result.message}"))

        self.stdout.write(self.style.SUCCESS(f"Done. Sent: {sent}, skipped: {skipped}."))
