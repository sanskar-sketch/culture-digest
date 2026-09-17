"""The send: choose users, choose events, read one, send.

The whole desk narrows to this page. Users come in from the Users tab
already ticked. The events offered are the ones the matching would pick
for them - scored on their interests, where they are and how far they'll
go, what they'll pay, and what they've said about past picks - each
marked with how many of the chosen users it suits. The editor ticks the
ones to send, reads what one user would get, and sends.

Each user still gets their own email. The pool is a shortlist, not a
broadcast: within it the matching ranks for each person, orders by score
and by what ends soonest, and writes the reason each pick suits them.
A user the pool has too little for is skipped, not padded.
"""

from collections import defaultdict

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from desk.permissions import staff_required
from opportunities import research
from opportunities.models import Opportunity, Tag
from readers.models import Reader
from recommendations import drafts, matching
from recommendations.models import IssueDraft
from siteconfig.models import SiteConfig

# How many suggestions arrive pre-ticked. The composer still chooses, per
# user, what reaches the FOR YOU floor and fits the sections; ticking is the
# editor's shortlist, so it starts generous.
PRETICKED = 30


def _ids(raw) -> list[int]:
    out = []
    for piece in (raw or "").split(","):
        piece = piece.strip()
        if piece.isdigit():
            out.append(int(piece))
    return out


def _readers(request):
    raw = request.GET.get("r") or request.POST.get("r") or ""
    ids = _ids(raw)
    return list(Reader.objects.filter(pk__in=ids, is_active=True).order_by("email")), raw


def _narrowing(request):
    """The interests picked on the Users page, carried through to here.

    Picking "comedy" to choose who to write to and then being offered
    everything is the kind of mismatch that makes a desk feel wrong. If
    those interests came with the users, only events carrying them are
    offered, and the page says so with a way to drop it.
    """
    raw = request.GET.get("t") or request.POST.get("t") or ""
    slugs = [s.strip() for s in raw.split(",") if s.strip()]
    tags = list(Tag.objects.filter(slug__in=slugs)) if slugs else []
    if not tags:
        return [], None, ""
    pool = set(Opportunity.objects.filter(tags__in=tags)
               .values_list("pk", flat=True).distinct())
    return tags, pool, raw


def suggestions(readers, pool=None):
    """Events the matching would pick for these users, pooled and ranked.

    One row per event: how many of the users it suits, the best score it
    got, and for whom. Sorted by reach then score, so the event most of
    them would get comes first.
    """
    per_event = defaultdict(lambda: {"suits": [], "best": 0.0})
    per_user = SiteConfig.load().recommendations_per_send
    for reader in readers:
        for match in matching.top_matches_for_reader(reader, limit=per_user, pool=pool):
            row = per_event[match.opportunity.pk]
            row["suits"].append(reader)
            row["best"] = max(row["best"], match.score)
            row["event"] = match.opportunity
    rows = sorted(per_event.values(), key=lambda r: (-len(r["suits"]), -r["best"]))
    for i, row in enumerate(rows):
        row["preticked"] = i < PRETICKED
        row["share"] = round(len(row["suits"]) / len(readers) * 100) if readers else 0
    return rows


@staff_required
def send(request):
    readers, raw = _readers(request)
    if not readers:
        messages.warning(request, "Tick some users first.")
        return redirect("desk:readers_list")

    narrowing, pool, tags_raw = _narrowing(request)

    if request.method == "POST":
        return _act(request, readers, raw)

    rows = suggestions(readers, pool=pool)
    chosen = {r["event"].pk for r in rows if r["preticked"]}

    # Arriving from one event on the Events page - "send this to the users
    # it's good for" - starts with that event ticked. It is only offered
    # here if the matching would pick it for someone: an event is good for
    # a user by interest, but it is sent to them only if it is published
    # and suits where they are, what they'll pay and when. Saying so beats
    # a silently missing row.
    asked = set(_ids(request.GET.get("e")))
    if asked:
        offered = {r["event"].pk for r in rows}
        chosen |= asked & offered
        missing = list(Opportunity.objects.filter(pk__in=asked - offered)
                       .values_list("title", flat=True))
        if missing:
            messages.info(request, "“" + "”, “".join(missing) + "” isn't in this list: only "
                          "events in circulation that suit a user on area, budget and dates are "
                          "offered. Their other picks are below.")

    # Reading one user's draft: the ticked events are the ones it was
    # written from, so what's ticked and what's shown always agree.
    preview_reader = draft = preview = None
    wanted = request.GET.get("preview")
    if wanted:
        preview_reader = next((r for r in readers if str(r.pk) == wanted), None)
        if preview_reader:
            draft = drafts.latest(preview_reader)
            preview = drafts.preview(draft) if draft else None
            if draft and draft.pool is not None:
                chosen = set(draft.pool)

    config = SiteConfig.load()
    return render(request, "desk/send.html", {
        "page_title": f"Send to {len(readers)} user{'' if len(readers) == 1 else 's'}",
        "breadcrumbs": [("Users", reverse("desk:readers_list")), ("Send", None)],
        "readers": readers,
        "raw": raw,
        "rows": rows,
        "narrowing": narrowing,
        "tags_raw": tags_raw,
        "per_send": config.recommendations_per_send,
        "min_stars": config.min_for_you_stars,
        "preview": preview,
        "draft": draft,
        "preview_reader": preview_reader,
        "writing": bool(draft and draft.status in (IssueDraft.Status.BUILDING, IssueDraft.Status.SENDING)),
        "star_choices": [x / 2 for x in range(2, 11)],
        "chosen": chosen,
        "drafted_readers": [r for r in readers if (d := drafts.latest(r)) and d.status == IssueDraft.Status.READY],
    })


def _act(request, readers, raw):
    action = request.POST.get("action")
    narrowing, pool, tags_raw = _narrowing(request)
    back = f"{reverse('desk:send')}?r={raw}" + (f"&t={tags_raw}" if tags_raw else "")

    if action == "research":
        from recommendations import ai

        if not ai.is_enabled("classify_opportunities"):
            messages.warning(request, "AI is not configured, or event research is switched "
                                      "off in Settings → AI assistance.")
        else:
            started = research.for_readers(Reader.objects.filter(pk__in=[r.pk for r in readers]))
            messages.success(request, (
                f"Searching for events for {', '.join(started)}. They arrive under Events, "
                "Waiting for you, with sources - accept the ones you want and come back here."
                if started else
                "These users' interests all have live events already, or research is "
                "already running."))
        return redirect(back)

    chosen_events = [int(x) for x in request.POST.getlist("event") if str(x).isdigit()]
    wanted = request.POST.get("preview_reader")
    reader = next((r for r in readers if str(r.pk) == wanted), readers[0])
    at_preview = f"{back}&preview={reader.pk}"

    if action == "save_edits":
        draft = drafts.latest(reader)
        if not draft or draft.status != IssueDraft.Status.READY:
            messages.warning(request, "There's no finished draft to edit for "
                                      f"{reader.email} - press Preview one first.")
            return redirect(at_preview)
        drafts.apply_edits(draft, request.POST)
        messages.success(request, f"Edits kept for {reader.email}. This is exactly what "
                                  "they'll get when you press Write & send.")
        return redirect(at_preview)

    if action == "discard_edits":
        drafts.build(reader, pool=chosen_events or (drafts.latest(reader) or IssueDraft()).pool,
                     user=request.user)
        messages.info(request, f"Writing {reader.email}'s week again from scratch.")
        return redirect(at_preview)

    if not chosen_events:
        messages.warning(request, "Tick at least one event.")
        return redirect(back)

    if action == "preview":
        # The week already written from these events - with any edits - is
        # kept. Write it again is its own button.
        existing = drafts.latest(reader)
        if not (existing and existing.pool == sorted(set(chosen_events)) and existing.status in
                (IssueDraft.Status.READY, IssueDraft.Status.BUILDING)):
            drafts.build(reader, pool=chosen_events, user=request.user)
        return redirect(at_preview)

    if action == "send":
        drafts.send_to(readers, pool=chosen_events, user=request.user)
        sent = pending = 0
        for r in readers:
            draft = drafts.latest(r)
            if draft is None:
                continue
            if draft.status == IssueDraft.Status.SENT:
                sent += 1
            elif draft.status in (IssueDraft.Status.EMPTY, IssueDraft.Status.FAILED):
                messages.warning(request, f"{r.email}: {draft.message}")
            else:
                pending += 1
        if sent:
            messages.success(request, f"Sent to {sent} user{'' if sent == 1 else 's'}, each "
                                      "their own culture week from the events you chose.")
        if pending:
            messages.success(request, (
                f"Sending to {pending} user{'' if pending == 1 else 's'} in the background. "
                "Anyone whose week you read goes exactly as you left it; the rest are written "
                "first, about a minute each. Each appears under every newsletter sent as it goes."))
        if not sent and not pending:
            messages.warning(request, "Nobody was sent anything - the chosen events weren't "
                                      "a strong enough match for them. Tick more, or press "
                                      "Find events with AI.")
        return redirect("desk:readers_list")

    return redirect(back)
