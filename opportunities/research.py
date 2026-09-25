"""Turning researched events into draft listings.

The model searches; this decides what is allowed into the catalogue. It is
deliberately suspicious of its input, because that input is the only place
in the product where facts arrive from outside:

* a listing with no source is dropped, not saved;
* a booking URL that isn't a URL is dropped;
* category, price tier and interests are resolved against our own tables,
  never taken as given;
* dates that don't parse become blank rather than wrong;
* anything already in the catalogue is skipped rather than duplicated.

Everything that survives is saved as a **draft**, so nothing researched can
reach a reader until an editor has published it.

Research takes tens of seconds, which is longer than a web request may
live, so `start` runs it on a thread and the editor refreshes the page.
"""

from __future__ import annotations

import logging
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import parse_qsl, urlparse

from django.db import close_old_connections
from django.utils.text import slugify

from .models import RELEASE_CATEGORIES, Category, Opportunity, Tag
from .reviews import public_url

logger = logging.getLogger(__name__)

# One research run at a time per process, so an impatient double-click
# doesn't spend the budget twice on the same interest.
_running: set[int] = set()
_lock = threading.Lock()


def _date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _choice(value, choices, default=""):
    allowed = {v for v, _ in choices}
    value = (value or "").strip().lower()
    return value if value in allowed else default


def _dial(value):
    try:
        return min(5, max(1, int(value)))
    except (TypeError, ValueError):
        return 3


def _unique_slug(title: str) -> str:
    base = slugify(title)[:200] or "listing"
    slug, n = base, 2
    while Opportunity.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"[:220]
        n += 1
    return slug


def _is_homepage(url: str) -> bool:
    """netflix.com, itv.com/ or a site search - not the page for this thing."""
    parsed = urlparse(url)
    if parsed.path in ("", "/") and not parsed.query:
        return True
    return ("/search" in parsed.path.lower()
            or any(k in ("q", "query", "s", "search") for k, _ in parse_qsl(parsed.query)))


# Where facts can be found but a reader shouldn't be sent.
_NOT_FOR_READERS = ("reddit.com", "x.com", "twitter.com", "facebook.com", "instagram.com",
                    "tiktok.com", "quora.com", "threads.net")


def _site(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _for_readers(url: str) -> bool:
    site = _site(url)
    return not any(site == d or site.endswith("." + d) for d in _NOT_FOR_READERS)


def _same_site(a: str, b: str) -> bool:
    x, y = _site(a), _site(b)
    return bool(x) and (x == y or x.endswith("." + y) or y.endswith("." + x))


# What research writes when it has no venue.
_NO_PLACE = {"-", "—", "–", "n/a", "na", "none", "unknown", "tbc", "tba", "not given"}


def link_status(url: str, timeout: float = 8.0) -> int:
    """The HTTP status a reader would get, or 0 if it couldn't be reached."""
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; TheEtherLinkCheck/1.0)", "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - http(s) only
            response.read(512)
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


_FILLER_WORDS = {"the", "a", "an", "and", "at", "of"}


def title_key(title: str) -> str:
    """A title as words only, for spotting the same thing named twice.

    "Ahmedabad International Film Festival 2026" and "Ahmedabad
    International Film Festival" are one festival; research found it
    under two interests and saved it twice.
    """
    words = re.sub(r"\b(?:19|20)\d{2}\b", " ", title.lower())
    words = re.sub(r"[^\w]+", " ", words)
    return " ".join(w for w in words.split() if w not in _FILLER_WORDS)


def _already_have(row) -> bool:
    """Same booking link, or the same title near enough - either means we have it."""
    url = public_url(row.get("booking_url") or "")
    if url and Opportunity.objects.filter(booking_url=url).exists():
        return True
    title = (row.get("title") or "").strip()
    key = title_key(title)
    if not key:
        return False
    first_word = key.split()[0]
    return any(title_key(other) == key for other in
               Opportunity.objects.filter(title__icontains=first_word).values_list("title", flat=True))


def save_drafts(payload: dict, interest: Tag | None = None,
                check_links: bool = False) -> list[Opportunity]:
    """Create draft listings from a research payload. Returns what was created.

    With `check_links`, each link is opened first: one that is gone (404 or
    410) is replaced by a working source page or the find is dropped, and
    what the check found is noted for whoever reviews it.
    """
    from django.utils import timezone

    today = timezone.localdate()
    created = []
    for row in payload.get("listings") or []:
        title = (row.get("title") or "").strip()
        booking_url = public_url(row.get("booking_url") or "")
        sources = [{**s, "url": public_url(str(s["url"]))} for s in (row.get("sources") or [])
                   if isinstance(s, dict) and str(s.get("url", "")).startswith("http")]

        # The three things that make a listing worth having at all.
        if not title or not sources:
            continue
        if not booking_url.startswith("http"):
            # Without somewhere to send a reader it is not yet a listing;
            # fall back to the page the facts came from, if it's one a
            # reader could be sent to.
            booking_url = next((s["url"] for s in sources if _for_readers(s["url"])), "")
            if not booking_url:
                continue
        if _is_homepage(booking_url):
            # A reader sent to a channel's front page has to go looking. A
            # page on the same site for this thing is better; a forum thread
            # or a schedule blog elsewhere is not.
            specific = next((s["url"] for s in sources if not _is_homepage(s["url"])
                             and _same_site(s["url"], booking_url)), None)
            if specific:
                booking_url = specific
        # Research is told to skip what's over; this makes sure of it.
        last_day = _date(row.get("end_date")) or _date(row.get("start_date"))
        if last_day and last_day < today and (row.get("category") or "") not in RELEASE_CATEGORIES:
            continue
        if _already_have(row):
            continue

        link_note = ""
        if check_links:
            status = link_status(booking_url)
            if status in (404, 410):
                working = next((s["url"] for s in sources if s["url"] != booking_url
                                and _for_readers(s["url"]) and not _is_homepage(s["url"])
                                and 0 < link_status(s["url"]) < 400), None)
                if not working:
                    logger.info("Dropped %r: its link is gone (HTTP %s)", title, status)
                    continue
                booking_url, link_note = working, " The link AI gave was dead; this is its source page."
            elif status == 0 or status >= 400:
                link_note = f" The link didn't open for us (HTTP {status or 'no answer'}) - check it."
            if _is_homepage(booking_url):
                link_note += " The link is a site's front page or search, not this item's page - find the right one."

        category = _choice(row.get("category"), Category.choices, Category.OTHER)
        opportunity = Opportunity.objects.create(
            title=title[:200],
            slug=_unique_slug(title),
            category=category,
            # A book, an album, a series: nobody travels to it.
            is_online=category in RELEASE_CATEGORIES,
            description=(row.get("description") or "").strip(),
            editorial_note=("Researched by AI. Check the date, the price and that it "
                            "is still on before publishing." + link_note),
            price_tier=_choice(row.get("price_tier"), Opportunity.PriceTier.choices,
                               Opportunity.PriceTier.MODERATE),
            price_display=(row.get("price_display") or "").strip()[:60],
            location_name=("" if (row.get("location_name") or "").strip().lower() in _NO_PLACE
                           else (row.get("location_name") or "").strip())[:200],
            location_area=((row.get("location_area") or "").strip()
                           or ("UK" if category in RELEASE_CATEGORIES else ""))[:120],
            booking_url=booking_url[:500],
            start_date=_date(row.get("start_date")),
            end_date=_date(row.get("end_date")),
            mainstream_to_unusual=_dial(row.get("mainstream_to_unusual")),
            intimate_to_large_scale=_dial(row.get("intimate_to_large_scale")),
            status=Opportunity.Status.DRAFT,
            found_by_ai=True,
            sources=[{"title": str(s.get("title", ""))[:200], "url": str(s["url"])[:500]}
                     for s in sources],
        )

        slugs = [str(s) for s in (row.get("tags") or [])]
        tags = list(Tag.objects.filter(slug__in=slugs))
        if interest is not None and interest not in tags:
            tags.append(interest)
        opportunity.tags.set(tags)
        created.append(opportunity)
    return created


def run(interest: Tag, area: str = "", count: int = 5) -> dict:
    """Research one interest and save what comes back. Synchronous."""
    from recommendations import ai

    payload = ai.research_listings(interest.name, area=area, count=count,
                                   category_hint=interest.category or "")
    if payload is None:
        return {"created": [], "notes": "", "ok": False}
    created = save_drafts(payload, interest=interest, check_links=True)
    return {"created": created, "notes": payload.get("notes", ""),
            "dropped": payload.get("dropped", 0), "ok": True}


def start(interest: Tag, area: str = "", count: int = 5) -> bool:
    """Research on a background thread. False if one is already running for it."""
    with _lock:
        if interest.pk in _running:
            return False
        _running.add(interest.pk)

    def work():
        try:
            result = run(interest, area=area, count=count)
            logger.info("Research for %r created %d draft(s)",
                        interest.name, len(result["created"]))
        except Exception:
            logger.exception("Research thread failed for %r", interest.name)
        finally:
            close_old_connections()
            with _lock:
                _running.discard(interest.pk)

    threading.Thread(target=work, daemon=True, name=f"research-{interest.pk}").start()
    return True


def is_running(interest: Tag) -> bool:
    with _lock:
        return interest.pk in _running


# How many different places one round of research covers. A reader whose
# city nobody else lives in still needs events found where they are.
MAX_AREAS = 4


def gaps_for(reader_ids, area: str = "", limit: int = 5):
    """The interests these readers hold that have the fewest events near them.

    Counted near them, not everywhere: a hundred London plays do nothing
    for a reader in Ahmedabad, so for that area plays still read as a gap.
    """
    from django.db.models import Count, Q

    live = Q(opportunities__status=Opportunity.Status.PUBLISHED)
    if area:
        live &= Q(opportunities__location_area__icontains=area) | Q(opportunities__is_online=True)
    return list(Tag.objects
                .annotate(readers=Count("interested_readers", distinct=True,
                                        filter=Q(interested_readers__in=reader_ids)),
                          live=Count("opportunities", distinct=True, filter=live))
                .filter(readers__gt=0)
                .order_by("live", "-readers", "name")[:limit])


def for_readers(readers=None, limit: int = 5, count: int = 4) -> list[str]:
    """Research the interests these readers have most and you have least for.

    With no readers given, every active reader counts. Searched area by
    area, busiest first, so someone living on their own in a city is not
    quietly left out of every search. Returns what research started for,
    as "plays in Ahmedabad".
    """
    from readers.models import Reader

    readers = readers if readers is not None else Reader.objects.filter(is_active=True)
    reader_ids = list(readers.values_list("pk", flat=True))
    if not reader_ids:
        return []

    by_area: dict[str, list[int]] = {}
    for pk, where in Reader.objects.filter(pk__in=reader_ids).values_list("pk", "location"):
        by_area.setdefault((where or "").strip(), []).append(pk)
    areas = sorted(by_area.items(), key=lambda pair: (-len(pair[1]), pair[0]))[:MAX_AREAS]
    each = max(1, limit // len(areas))

    started = []
    for area, ids in areas:
        for tag in gaps_for(ids, area=area, limit=each):
            if len(started) >= limit:
                break
            if start(tag, area=area, count=count):
                started.append(f"{tag.name} in {area}" if area else tag.name)
    return started


# ---------------------------------------------------------------------------
# This week's releases
# ---------------------------------------------------------------------------

_releases_running = threading.Event()


def releases_window(today=None):
    """From a few days back - so "just out" counts - to a week ahead."""
    from datetime import timedelta

    from django.utils import timezone

    today = today or timezone.localdate()
    return today - timedelta(days=3), today + timedelta(days=7)


def reader_interests_for(kind: str, limit: int = 12) -> list[str]:
    """The interests active readers hold most, to lean the search towards them."""
    from django.db.models import Count, Q

    from readers.models import Reader

    active = Reader.objects.filter(is_active=True)
    rows = (Tag.objects.annotate(n=Count("interested_readers", distinct=True,
                                         filter=Q(interested_readers__in=active)))
            .filter(n__gt=0).filter(Q(category=kind) | Q(category="") | Q(category__in=[
                Category.MUSIC, Category.FILM, Category.EXHIBITION, Category.TALK]))
            .order_by("-n", "name")[:limit])
    return [t.name for t in rows]


def run_releases(kinds=("book", "listen", "watch"), count: int = 6) -> dict:
    """Search each kind of release for the coming week and save drafts. Synchronous."""
    from recommendations import ai

    start, end = releases_window()
    created, notes = [], []
    for kind in kinds:
        payload = ai.research_releases(kind, start, end, reader_interests_for(kind), count=count)
        if payload is None:
            continue
        created += save_drafts(payload, check_links=True)
        if payload.get("notes"):
            notes.append(f"{kind}: {payload['notes']}")
    return {"created": created, "notes": " ".join(notes)}


def start_releases() -> bool:
    """Search for releases on a background thread. False if one is already running."""
    if _releases_running.is_set():
        return False
    _releases_running.set()

    def work():
        try:
            result = run_releases()
            logger.info("Release research created %d draft(s)", len(result["created"]))
        except Exception:
            logger.exception("Release research failed")
        finally:
            close_old_connections()
            _releases_running.clear()

    threading.Thread(target=work, daemon=True, name="research-releases").start()
    return True
