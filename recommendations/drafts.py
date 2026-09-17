"""Culture weeks written in the background, read at the desk, then sent.

Writing a whole week for one reader takes AI the better part of a minute.
A web request on this host is killed after thirty seconds, so nothing that
writes an issue may run inside one. The desk asks for a draft, the work
happens on one background worker, the page refreshes until it's ready, and
Write & send sends exactly the draft that was read - edits and all - without
asking AI to write it again.

One worker, not a thread per reader: sending to fifty readers must not open
fifty simultaneous AI calls, and the order they finish in should be the
order they were asked for.
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from . import compose
from .models import IssueDraft

logger = logging.getLogger(__name__)

# A draft still "writing" after this long was lost - most likely a restart.
STALE_AFTER = timedelta(minutes=15)

_jobs: "queue.Queue[tuple]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def _normal_pool(pool):
    return None if pool is None else sorted({int(x) for x in pool})


def _run(fn, *args) -> None:
    if getattr(settings, "DRAFTS_INLINE", False):
        _guarded(fn, *args)
        return
    global _worker
    _jobs.put((fn, args))
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work_forever, daemon=True, name="issue-drafts")
            _worker.start()


def _work_forever() -> None:
    while True:
        fn, args = _jobs.get()
        try:
            _guarded(fn, *args)
        finally:
            close_old_connections()
            _jobs.task_done()


def _guarded(fn, draft_id) -> None:
    try:
        fn(draft_id)
    except Exception as exc:
        logger.exception("Issue draft %s failed", draft_id)
        IssueDraft.objects.filter(pk=draft_id).update(
            status=IssueDraft.Status.FAILED, message=f"Something went wrong: {exc}"[:500],
            updated_at=timezone.now())


# ---------------------------------------------------------------------------

def latest(reader) -> IssueDraft | None:
    """This reader's current draft, if any, with a lost one marked as such."""
    draft = IssueDraft.objects.filter(reader=reader).first()
    if draft and draft.status in (IssueDraft.Status.BUILDING, IssueDraft.Status.SENDING) \
            and timezone.now() - draft.updated_at > STALE_AFTER:
        draft.status = IssueDraft.Status.FAILED
        draft.message = "This took too long and was abandoned - the server may have restarted. Try again."
        draft.save(update_fields=["status", "message", "updated_at"])
    return draft


def build(reader, *, pool=None, user=None, send_when_ready: bool = False) -> IssueDraft:
    """Start writing this reader's week. Replaces any draft not yet sent."""
    # One draft per reader. A sent one has done its job - the issue is in the
    # records - so it goes too.
    IssueDraft.objects.filter(reader=reader).delete()
    draft = IssueDraft.objects.create(
        reader=reader, created_by=user if getattr(user, "is_authenticated", False) else None,
        pool=_normal_pool(pool), status=IssueDraft.Status.BUILDING,
        send_when_ready=send_when_ready,
        message="Writing this week's issue. It takes about a minute.")
    _run(_build, draft.pk)
    return draft


def _build(draft_id: int) -> None:
    from siteconfig.models import SiteConfig

    from .sending import _why_thin

    draft = IssueDraft.objects.select_related("reader").get(pk=draft_id)
    composed = compose.compose(draft.reader, pool=draft.pool)
    draft.content = compose.to_json(composed)
    needed = SiteConfig.load().min_recommendations
    if len(composed.picks) < needed:
        draft.status = IssueDraft.Status.EMPTY
        draft.message = f"Skipped: {composed.message} {_why_thin(draft.reader, draft.pool)}".strip()
        draft.save(update_fields=["content", "status", "message", "updated_at"])
        return
    draft.status = IssueDraft.Status.READY
    draft.message = composed.message + ("" if composed.written_by_ai else
                                        " Written from the template: AI is off or didn't answer.")
    draft.save(update_fields=["content", "status", "message", "updated_at"])
    if draft.send_when_ready:
        _send(draft.pk)


def send(draft: IssueDraft) -> None:
    """Send a draft as it stands, or as soon as it has been written."""
    if draft.status == IssueDraft.Status.READY:
        draft.status = IssueDraft.Status.SENDING
        draft.message = "Sending."
        draft.save(update_fields=["status", "message", "updated_at"])
        _run(_send, draft.pk)
    elif draft.status == IssueDraft.Status.BUILDING:
        draft.send_when_ready = True
        draft.save(update_fields=["send_when_ready", "updated_at"])


def _send(draft_id: int) -> None:
    from .sending import send_issue_for_reader

    draft = IssueDraft.objects.select_related("reader").get(pk=draft_id)
    composed = compose.from_json(draft.reader, draft.content)
    result = send_issue_for_reader(draft.reader, pool=draft.pool, composed=composed)
    draft.status = IssueDraft.Status.SENT if result.sent else IssueDraft.Status.EMPTY
    draft.message = result.message
    draft.issue = result.issue
    draft.save(update_fields=["status", "message", "issue", "updated_at"])


def send_to(readers, *, pool=None, user=None) -> dict:
    """Write & send for several readers: a ready draft for the same events is
    sent as it stands; anyone without one has theirs written, then sent."""
    wanted = _normal_pool(pool)
    counts = {"as_read": 0, "written_then_sent": 0}
    for reader in readers:
        draft = latest(reader)
        if draft and draft.pool == wanted and draft.status == IssueDraft.Status.READY:
            send(draft)
            counts["as_read"] += 1
        elif draft and draft.pool == wanted and draft.status == IssueDraft.Status.BUILDING:
            send(draft)
            counts["written_then_sent"] += 1
        else:
            build(reader, pool=pool, user=user, send_when_ready=True)
            counts["written_then_sent"] += 1
    return counts


def preview(draft: IssueDraft) -> dict | None:
    """The rendered email and its picks, for a draft that has been written."""
    from .emailing import render_composed

    if not draft or not draft.content or draft.status not in (
            IssueDraft.Status.READY, IssueDraft.Status.SENDING, IssueDraft.Status.SENT):
        return None
    composed = compose.from_json(draft.reader, draft.content)
    subject, html, text = render_composed(composed, tracked=False)
    return {
        "ok": True, "subject": subject, "html": html, "text": text, "composed": composed,
        "message": draft.message,
        "picks": [
            {"title": p.opportunity.title, "score": p.score, "event_id": p.event_id,
             "rationale": p.rationale, "verdict": p.hook, "hook": p.hook, "caveat": p.caveat,
             "stars": p.stars, "section": p.section, "is_top": p.is_top,
             "timing_label": p.timing_label, "edited": p.edited}
            for p in composed.picks
        ],
    }


def apply_edits(draft: IssueDraft, data) -> int:
    """Take an editor's changes to a draft. Returns how many picks changed.

    `data` is the posted form: intro, closing, and per pick stars_<id>,
    hook_<id>, rationale_<id>, caveat_<id>. A changed rating can move a
    pick into or out of the top, so the issue is arranged again.
    """
    from siteconfig.models import SiteConfig

    composed = compose.from_json(draft.reader, draft.content)
    changed = 0
    for field in ("intro", "closing"):
        if field in data:
            setattr(composed, field, (data.get(field) or "").strip())
    for pick in composed.picks:
        key = str(pick.event_id)
        before = (pick.stars, pick.hook, pick.rationale, pick.caveat)
        if f"rationale_{key}" in data:
            pick.rationale = (data.get(f"rationale_{key}") or "").strip() or pick.rationale
        if f"hook_{key}" in data:
            pick.hook = (data.get(f"hook_{key}") or "").strip()
        elif f"verdict_{key}" in data:
            pick.hook = (data.get(f"verdict_{key}") or "").strip()
        if f"caveat_{key}" in data:
            pick.caveat = (data.get(f"caveat_{key}") or "").strip()
        if data.get(f"stars_{key}"):
            try:
                pick.stars = compose.round_half(float(Decimal(data.get(f"stars_{key}"))))
            except (InvalidOperation, ValueError):
                pass
        if (pick.stars, pick.hook, pick.rationale, pick.caveat) != before:
            pick.edited = True
            changed += 1
    composed.picks = compose.arrange(composed.picks, SiteConfig.load(), respect_floor=False)
    draft.content = compose.to_json(composed)
    draft.save(update_fields=["content", "updated_at"])
    return changed
