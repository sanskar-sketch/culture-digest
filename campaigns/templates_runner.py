"""Running a saved template: the one place both kinds are executed.

A newsletter template sends each reader in its audience their own picks,
through the same path the scheduled newsletter uses. A campaign template
becomes a real Campaign and goes out through the campaign pipeline, so it
gets delivery rows and a results tab like any other.

Called from the desk (Run now) and from the scheduler (a due frequency).
Returns a plain report; raising here would take a whole scheduler pass
down with it.
"""

from __future__ import annotations

import logging

from recommendations import ai
from recommendations.sending import send_issue_for_reader

from .models import SavedTemplate
from .sending import WEB_BUDGET_SECONDS, send_campaign

logger = logging.getLogger(__name__)


def refresh_audience(template: SavedTemplate) -> str | None:
    """If the template asks AI to choose, choose. Returns a note or None."""
    if not template.auto_select_audience or not ai.is_enabled("write_campaigns"):
        return None
    from opportunities.models import Category, Tag

    suggestion = ai.suggest_audience(template)
    if not suggestion:
        return "AI could not pick an audience this time; used the saved one."
    allowed = {v for v, _ in Category.choices}
    template.audience_categories = [c for c in suggestion.get("categories") or []
                                    if c in allowed]
    template.audience_location = (suggestion.get("location") or "")[:120]
    template.save(update_fields=["audience_categories", "audience_location", "updated_at"])
    template.audience_tags.set(Tag.objects.filter(slug__in=suggestion.get("tags") or []))
    return f"AI set the audience: {template.audience_description()}."


def run(template: SavedTemplate, *, budget_seconds: float = WEB_BUDGET_SECONDS,
        created_by=None) -> dict:
    """Execute one run. {"ok", "sent", "skipped", "failed", "message", "campaign"}."""
    report = {"ok": True, "sent": 0, "skipped": 0, "failed": 0,
              "message": "", "campaign": None}
    note = refresh_audience(template)

    if template.kind == SavedTemplate.Kind.CAMPAIGN:
        if not (template.brief.strip() or template.body.strip()):
            report.update(ok=False, message="Nothing to send: the template has no brief "
                                            "and no body.")
            return report
        campaign = template.make_campaign(created_by=created_by)
        campaign.status = campaign.Status.SCHEDULED
        campaign.save(update_fields=["status", "updated_at"])
        run_result = send_campaign(campaign, budget_seconds=budget_seconds)
        template.mark_run()
        report.update(sent=run_result.sent, failed=run_result.failed,
                      campaign=campaign,
                      message=f"Campaign “{campaign.name}”: {run_result.message}")
    else:
        readers = list(template.audience())
        if not readers:
            report.update(ok=False, message="Nobody matches this template's audience.")
            return report
        budget = ai.TimeBudget(budget_seconds)
        for reader in readers:
            if budget.exhausted():
                report["message"] = (f"Out of time after {report['sent'] + report['skipped']} "
                                     f"of {len(readers)} - run it again for the rest.")
                break
            try:
                result = send_issue_for_reader(reader)
                report["sent" if result.sent else "skipped"] += 1
            except Exception:
                logger.exception("Template %r: send to %s failed", template.name, reader.email)
                report["failed"] += 1
        template.mark_run()
        if not report["message"]:
            report["message"] = (f"Newsletter sent to {report['sent']}, "
                                 f"skipped {report['skipped']}"
                                 + (f", failed {report['failed']}" if report["failed"] else "")
                                 + ".")
    if note:
        report["message"] = f"{note} {report['message']}"
    return report


def run_due(now=None, *, budget_seconds: float) -> list[str]:
    """For the scheduler: run every active template whose time has come."""
    lines = []
    for template in SavedTemplate.objects.filter(status=SavedTemplate.Status.ACTIVE):
        due, why = template.due_for_a_run(now)
        if not due:
            continue
        try:
            report = run(template, budget_seconds=budget_seconds)
            lines.append(f"Template '{template}': {report['message']}")
        except Exception:
            logger.exception("Template %r failed to run", template.name)
            lines.append(f"Template '{template}': failed - see the logs.")
    return lines
