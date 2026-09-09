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
import threading
from datetime import datetime

from django.db import close_old_connections
from django.utils.text import slugify

from .models import Category, Opportunity, Tag

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


def _already_have(row) -> bool:
    """Same booking link, or same title - either means we have it."""
    url = (row.get("booking_url") or "").strip()
    if url and Opportunity.objects.filter(booking_url=url).exists():
        return True
    title = (row.get("title") or "").strip()
    return bool(title) and Opportunity.objects.filter(title__iexact=title).exists()


def save_drafts(payload: dict, interest: Tag | None = None) -> list[Opportunity]:
    """Create draft listings from a research payload. Returns what was created."""
    created = []
    for row in payload.get("listings") or []:
        title = (row.get("title") or "").strip()
        booking_url = (row.get("booking_url") or "").strip()
        sources = [s for s in (row.get("sources") or [])
                   if isinstance(s, dict) and str(s.get("url", "")).startswith("http")]

        # The three things that make a listing worth having at all.
        if not title or not sources:
            continue
        if not booking_url.startswith("http"):
            # Without somewhere to send a reader it is not yet a listing;
            # fall back to the page the facts came from.
            booking_url = sources[0]["url"]
        if _already_have(row):
            continue

        opportunity = Opportunity.objects.create(
            title=title[:200],
            slug=_unique_slug(title),
            category=_choice(row.get("category"), Category.choices, Category.OTHER),
            description=(row.get("description") or "").strip(),
            editorial_note="Researched by AI. Check the date, the price and that it "
                           "is still on before publishing.",
            price_tier=_choice(row.get("price_tier"), Opportunity.PriceTier.choices,
                               Opportunity.PriceTier.MODERATE),
            price_display=(row.get("price_display") or "").strip()[:60],
            location_name=(row.get("location_name") or "").strip()[:200],
            location_area=(row.get("location_area") or "").strip()[:120],
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
    created = save_drafts(payload, interest=interest)
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


def for_readers(readers=None, limit: int = 5, count: int = 4) -> list[str]:
    """Research the interests these readers have most and you have least for.

    With no readers given, every active reader counts. The area searched is
    where most of them are. Returns the interest names research started for.
    """
    from collections import Counter

    from django.db.models import Count, Q

    from readers.models import Reader

    readers = readers if readers is not None else Reader.objects.filter(is_active=True)
    reader_ids = list(readers.values_list("pk", flat=True))
    if not reader_ids:
        return []

    wanted = (Tag.objects
              .annotate(readers=Count("interested_readers", distinct=True,
                                      filter=Q(interested_readers__in=reader_ids)),
                        live=Count("opportunities", distinct=True,
                                   filter=Q(opportunities__status=Opportunity.Status.PUBLISHED)))
              .filter(readers__gt=0)
              .order_by("live", "-readers", "name")[:limit])

    areas = Counter(a for a in Reader.objects.filter(pk__in=reader_ids)
                    .values_list("location", flat=True) if a)
    area = areas.most_common(1)[0][0] if areas else ""

    return [tag.name for tag in wanted if start(tag, area=area, count=count)]
