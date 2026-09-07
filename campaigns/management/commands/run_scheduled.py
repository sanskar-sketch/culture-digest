"""The one command a cron job needs to call.

Sends whatever is due - campaigns whose time has come or that are part-way
through, and the newsletter when its cadence and send hour say so. Safe to
run as often as you like: nothing here sends twice.

The web service exposes the same pass at /tasks/run-scheduled/ for setups
without cron (see campaigns.views).
"""

from django.core.management.base import BaseCommand

from campaigns.scheduler import run_due


class Command(BaseCommand):
    help = ("Send due campaigns and, on its send day and hour, the newsletter. "
            "Run every few minutes from cron.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--budget-seconds", type=float, default=540.0,
            help="Stop starting new deliveries after this long, so overlapping runs "
                 "don't pile up. Whatever is left goes next time.")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Say what would be sent and why, without sending anything.")

    def handle(self, *args, **options):
        report = run_due(options["budget_seconds"], dry_run=options["dry_run"])
        for line in report["lines"]:
            style = self.style.WARNING if ("not now" in line or "next pass" in line
                                           or "nothing due" in line) else self.style.SUCCESS
            self.stdout.write(style(line))
