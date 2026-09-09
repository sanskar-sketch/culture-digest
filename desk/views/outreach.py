"""Send a newsletter to the readers behind a set of interests.

The rest of the desk is organised by object - listings, readers,
campaigns. This screen is organised by the question an editor actually
starts from: *we have something good for people who like X; who are they,
what would they get, and can I send it now?*

Three things on one page, in that order: pick the interests, see exactly
who that is, read what one of them would receive. Sending is the last
thing you do, after the preview, never before it.
"""

from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from desk.permissions import staff_required
from opportunities.models import Tag
from readers.models import Reader
from recommendations.sending import preview_issue_for_reader, send_issue_for_reader

# More than this and the page stops being a list you can read.
READER_LIMIT = 200


def _selected_tags(request) -> list[Tag]:
    slugs = request.GET.getlist("tag") or request.POST.getlist("tag")
    return list(Tag.objects.filter(slug__in=slugs).order_by("name"))


def _audience(tags, match_all: bool):
    """Active readers who picked these interests - their own, or inferred.

    A reader who typed an interest in has it added to their picks, and one
    AI inferred it for is included too: both are things we believe about
    them, and excluding the inferred ones would make this narrower than
    the matching that actually decides their newsletter.
    """
    qs = Reader.objects.filter(is_active=True)
    if not tags:
        return qs.none()
    if match_all:
        for tag in tags:
            qs = qs.filter(Q(interest_tags=tag) | Q(ai_inferred_tags=tag))
        return qs.distinct()
    return qs.filter(
        Q(interest_tags__in=tags) | Q(ai_inferred_tags__in=tags)
    ).distinct()


def _auto_tags(limit: int = 5) -> list[str]:
    """The interests worth sending about: most readers, and something to send.

    An interest nobody has is pointless; an interest with no published
    listing behind it produces an empty send. This wants both.
    """
    rows = (Tag.objects
            .annotate(readers=Count("interested_readers", distinct=True),
                      live=Count("opportunities",
                                 filter=Q(opportunities__status="published"),
                                 distinct=True))
            .filter(readers__gt=0, live__gt=0)
            .order_by("-readers", "-live")[:limit])
    return [tag.slug for tag in rows]


@staff_required
def send_by_interest(request):
    match_all = (request.GET.get("match") or request.POST.get("match")) == "all"

    if request.method == "POST":
        return _act(request, match_all)

    if request.GET.get("auto") and not request.GET.getlist("tag"):
        params = "&".join(f"tag={slug}" for slug in _auto_tags())
        return redirect(f"{request.path}?{params}" if params else request.path)

    tags = _selected_tags(request)
    audience = _audience(tags, match_all).order_by("email")
    readers = list(audience[:READER_LIMIT])

    preview = None
    preview_reader = None
    if request.GET.get("preview"):
        preview_reader = Reader.objects.filter(pk=request.GET["preview"]).first()
        if preview_reader:
            preview = preview_issue_for_reader(preview_reader)

    all_tags = (Tag.objects
                .annotate(readers=Count("interested_readers", distinct=True),
                          live=Count("opportunities",
                                     filter=Q(opportunities__status="published"),
                                     distinct=True))
                .order_by("-readers", "name"))

    return render(request, "desk/send_by_interest.html", {
        "page_title": "Send by interest",
        "page_blurb": "Pick the interests, see who that is, read what one of them "
                      "would get, then send. The email is the ordinary newsletter, "
                      "built for each reader individually.",
        "breadcrumbs": [("Send by interest", None)],
        "all_tags": all_tags,
        "selected_slugs": [tag.slug for tag in tags],
        "selected_tags": tags,
        "match_all": match_all,
        "audience_count": audience.count(),
        "readers": readers,
        "truncated": audience.count() > READER_LIMIT,
        "preview": preview,
        "preview_reader": preview_reader,
    })


def _act(request, match_all):
    action = request.POST.get("action")
    slugs = request.POST.getlist("tag")
    query = "&".join([f"tag={s}" for s in slugs] + (["match=all"] if match_all else []))
    back = f"{reverse('desk:send_by_interest')}?{query}"

    ids = request.POST.getlist("selected")
    readers = list(Reader.objects.filter(pk__in=ids, is_active=True))

    if action == "preview":
        if not readers:
            messages.warning(request, "Tick a reader to preview.")
            return redirect(back)
        return redirect(f"{back}&preview={readers[0].pk}")

    if action != "send":
        return redirect(back)

    if not readers:
        messages.warning(request, "Nobody selected.")
        return redirect(back)

    sent = skipped = failed = 0
    for reader in readers:
        try:
            result = send_issue_for_reader(reader)
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
        messages.success(request, f"Sent to {sent} reader{'' if sent == 1 else 's'}.")
    if skipped and not sent:
        messages.warning(request, "Nobody was sent anything - see the reasons above.")
    return redirect(back)


@staff_required
def reader_tags(request, pk):
    """Everything we believe about one reader's taste, in one place."""
    reader = get_object_or_404(Reader, pk=pk)
    return render(request, "desk/reader_tags.html", {
        "page_title": f"Tags for {reader.email}",
        "breadcrumbs": [("Readers", reverse("desk:readers_list")),
                        (reader.email, reverse("desk:readers_change", args=[reader.pk])),
                        ("Tags", None)],
        "reader": reader,
        "picked": reader.interest_tags.all().order_by("name"),
        "inferred": reader.ai_inferred_tags.all().order_by("name"),
        "avoid": reader.ai_avoid_tags.all().order_by("name"),
    })
