"""Everything that runs on a timer, in one place.

`run_due` sends whatever is due: campaigns whose time has come or that
are part-way through, then the newsletter on its send day once the send
hour has passed. It is resumable and idempotent - every piece of work is
recorded per reader, so calling it again only does what's left.

Two things call it. The `run_scheduled` management command, for a real
cron job. And the ping endpoint (see views.py), which starts it on a
background thread inside the web process: the ping returns at once, the
site keeps serving, and an external monitor hitting the URL every few
minutes stands in for cron at no cost.
"""

from __future__ import annotations

import logging
import threading

from django.core.cache import cache
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from recommendations.ai import TimeBudget
from recommendations.sending import send_scheduled_newsletter

from .models import Campaign
from .sending import send_campaign

logger = logging.getLogger(__name__)

LAST_RUN_KEY = "scheduler:last_run"

# One run at a time per process. The web service runs a single worker, so
# a process-level lock is enough; with several workers this would need to
# move to the cache or the database.
_running = threading.Lock()


def due_campaigns(now=None):
    now = now or timezone.now()
    return Campaign.objects.filter(
        Q(status=Campaign.Status.SCHEDULED, send_at__isnull=True)
        | Q(status=Campaign.Status.SCHEDULED, send_at__lte=now)
        | Q(status=Campaign.Status.SENDING)
    ).order_by("send_at", "pk")


def run_due(budget_seconds: float, dry_run: bool = False) -> dict:
    """One pass. Returns a report with a line per thing it did or declined."""
    budget = TimeBudget(budget_seconds)
    report = {"started_at": timezone.now(), "campaigns": [], "newsletter": None, "lines": []}

    campaigns = list(due_campaigns())
    if not campaigns:
        report["lines"].append("Campaigns: nothing due.")
    for campaign in campaigns:
        if budget.exhausted():
            report["lines"].append(f"Campaigns: out of time before '{campaign}' - next pass.")
            break
        if dry_run:
            n = campaign.audience().count()
            report["lines"].append(f"Would send '{campaign}' to {n} reader{'' if n == 1 else 's'}.")
            continue
        run = send_campaign(campaign, budget_seconds=max(budget.seconds - budget.spent, 1.0))
        report["campaigns"].append({"name": str(campaign), "sent": run.sent, "failed": run.failed,
                                    "remaining": run.remaining, "finished": run.finished})
        report["lines"].append(f"Campaign '{campaign}': {run.message}")

    newsletter = send_scheduled_newsletter(
        budget_seconds=max(budget.seconds - budget.spent, 1.0), dry_run=dry_run)
    report["newsletter"] = newsletter
    if not newsletter["ran"]:
        report["lines"].append(f"Newsletter: not now - {newsletter['why']}")
    elif dry_run:
        report["lines"].append(
            f"Newsletter: would send to {newsletter['remaining']} reader(s) - {newsletter['why']}")
    else:
        line = (f"Newsletter: {newsletter['sent']} sent, {newsletter['skipped']} skipped"
                + (f", {newsletter['failed']} failed" if newsletter["failed"] else ""))
        if newsletter["done"]:
            line += ". Everyone has been considered; today's send is complete."
        else:
            line += f". {newsletter['remaining']} reader(s) still to go - next pass."
        report["lines"].append(line)

    report["finished_at"] = timezone.now()
    report["seconds"] = round(budget.spent, 1)
    return report


def start_background_run(budget_seconds: float) -> bool:
    """Run `run_due` on a thread. False if a run is already in progress."""
    if not _running.acquire(blocking=False):
        return False

    def work():
        try:
            report = run_due(budget_seconds)
            cache.set(LAST_RUN_KEY, {
                "at": report["finished_at"], "seconds": report["seconds"],
                "lines": report["lines"],
            }, 7 * 24 * 3600)
            logger.info("Scheduler pass finished in %ss: %s",
                        report["seconds"], " | ".join(report["lines"]))
        except Exception:
            logger.exception("Scheduler pass failed")
            cache.set(LAST_RUN_KEY, {"at": timezone.now(), "seconds": None,
                                     "lines": ["Failed - see the logs."]}, 7 * 24 * 3600)
        finally:
            connection.close()  # this thread's DB connection, not the request's
            _running.release()

    threading.Thread(target=work, name="scheduler", daemon=True).start()
    return True


def is_running() -> bool:
    return _running.locked()


def last_run() -> dict | None:
    return cache.get(LAST_RUN_KEY)
