"""The one command a cron job needs to call.

Sends whatever is due - campaigns whose time has come or that are part-way
through, and the newsletter when its cadence and send hour say so. Safe to
run as often as you like: nothing here sends twice.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from campaigns.models import Campaign
from campaigns.sending import send_campaign
from recommendations.ai import TimeBudget


class Command(BaseCommand):
    help = ("Send due campaigns and, on its send day and hour, the newsletter. "
            "Run every 15 minutes from cron.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--budget-seconds", type=float, default=540.0,
            help="Stop starting new deliveries after this long, so overlapping runs "
                 "don't pile up. Whatever is left goes next time.")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Say what would be sent and why, without sending anything.")

    def handle(self, *args, **options):
        from siteconfig.models import SiteConfig

        now = timezone.now()
        budget = TimeBudget(options["budget_seconds"])
        dry_run = options["dry_run"]

        due = Campaign.objects.filter(
            Q(status=Campaign.Status.SCHEDULED, send_at__isnull=True)
            | Q(status=Campaign.Status.SCHEDULED, send_at__lte=now)
            | Q(status=Campaign.Status.SENDING)
        ).order_by("send_at", "pk")

        if not due.exists():
            self.stdout.write("Campaigns: nothing due.")
        for campaign in due:
            if budget.exhausted():
                self.stdout.write(self.style.WARNING(
                    f"Campaigns: out of time before '{campaign}' - next run."))
                break
            if dry_run:
                self.stdout.write(f"Would send '{campaign}' to "
                                  f"{campaign.audience().count()} reader(s).")
                continue
            run = send_campaign(
                campaign, budget_seconds=max(budget.seconds - budget.spent, 1.0))
            style = self.style.SUCCESS if run.finished else self.style.WARNING
            self.stdout.write(style(f"Campaign '{campaign}': {run.message}"))

        config = SiteConfig.load()
        should, why = config.should_send_now(now)
        if not should:
            self.stdout.write(f"Newsletter: not now - {why}")
        elif dry_run:
            self.stdout.write(f"Newsletter: would send - {why}")
        else:
            self.stdout.write(f"Newsletter: sending - {why}")
            call_command("send_newsletters", scheduled=True, stdout=self.stdout)
