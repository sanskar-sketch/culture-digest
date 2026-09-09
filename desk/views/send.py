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
from readers.models import Reader
from recommendations import matching
from recommendations.sending import preview_issue_for_reader, send_issue_for_reader
from siteconfig.models import SiteConfig

# How many events to consider per user when building the suggestions.
PER_USER = 8
# How many suggestions arrive pre-ticked.
PRETICKED = 6


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


def suggestions(readers):
    """Events the matching would pick for these users, pooled and ranked.

    One row per event: how many of the users it suits, the best score it
    got, and for whom. Sorted by reach then score, so the event most of
    them would get comes first.
    """
    per_event = defaultdict(lambda: {"suits": [], "best": 0.0})
    for reader in readers:
        for match in matching.top_matches_for_reader(reader, limit=PER_USER):
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

    if request.method == "POST":
        return _act(request, readers, raw)

    rows = suggestions(readers)
    return render(request, "desk/send.html", {
        "page_title": f"Send to {len(readers)} user{'' if len(readers) == 1 else 's'}",
        "breadcrumbs": [("Users", reverse("desk:readers_list")), ("Send", None)],
        "readers": readers,
        "raw": raw,
        "rows": rows,
        "per_send": SiteConfig.load().recommendations_per_send,
        "preview": None,
        "preview_reader": None,
        "chosen": {r["event"].pk for r in rows if r["preticked"]},
    })


def _act(request, readers, raw):
    action = request.POST.get("action")
    back = f"{reverse('desk:send')}?r={raw}"

    if action == "research":
        from recommendations import ai

        if not ai.is_enabled("classify_opportunities"):
            messages.warning(request, "AI is not configured, or event research is switched "
                                      "off in Settings → AI assistance.")
        else:
            started = research.for_readers(Reader.objects.filter(pk__in=[r.pk for r in readers]))
            messages.success(request, (
                f"Searching for events for {', '.join(started)}. They arrive under Events "
                "as drafts, with sources - publish the ones you want and come back here."
                if started else
                "These users' interests all have live events already, or research is "
                "already running."))
        return redirect(back)

    pool = [int(x) for x in request.POST.getlist("event") if str(x).isdigit()]
    if not pool:
        messages.warning(request, "Tick at least one event.")
        return redirect(back)

    if action == "preview":
        wanted = request.POST.get("preview_reader")
        reader = next((r for r in readers if str(r.pk) == wanted), readers[0])
        preview = preview_issue_for_reader(reader, pool=pool)
        rows = suggestions(readers)
        return render(request, "desk/send.html", {
            "page_title": f"Send to {len(readers)} user{'' if len(readers) == 1 else 's'}",
            "breadcrumbs": [("Users", reverse("desk:readers_list")), ("Send", None)],
            "readers": readers, "raw": raw, "rows": rows,
            "per_send": SiteConfig.load().recommendations_per_send,
            "preview": preview, "preview_reader": reader, "chosen": set(pool),
        })

    if action == "send":
        sent = skipped = failed = 0
        for reader in readers:
            try:
                result = send_issue_for_reader(reader, pool=pool)
            except Exception as exc:
                failed += 1
                messages.error(request, f"{reader.email}: send failed — {exc}")
                continue
            if result.sent:
                sent += 1
            else:
                skipped += 1
                messages.warning(request, f"{reader.email}: {result.message}")
        if sent:
            messages.success(request, f"Sent to {sent} user{'' if sent == 1 else 's'}, each "
                                      "their own email from the events you chose.")
        if not sent and not failed:
            messages.warning(request, "Nobody was sent anything - the chosen events weren't "
                                      "a strong enough match for them. Tick more, or press "
                                      "Find more with AI.")
        return redirect("desk:readers_list")

    return redirect(back)

