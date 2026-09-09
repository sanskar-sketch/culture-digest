"""Interests readers ask for, in their own words.

The signup form has a fixed list of interests and a box for anything it
doesn't cover. That box used to be read by nobody: the text was stored,
and the interest never existed, so nothing was ever tagged with it and no
listing could match it.

Now what a reader types becomes a real interest, marked as having come
from the public and counting how many people have asked for it. That
count is the queue: the interest ten readers typed and no listing carries
is the most valuable thing an editor can go and find.

Their taste is read into signals in the same pass - the summary and the
inferred interests an editor sees on the user's row.

Runs off the request. Someone signing up waits for a page, not for a
model, and a failure here must never cost us the signup.
"""

from __future__ import annotations

import logging
import threading

from django.db import close_old_connections
from django.utils.text import slugify

from opportunities.models import Category, Tag

logger = logging.getLogger(__name__)


def _valid_category(value: str) -> str:
    allowed = {v for v, _ in Category.choices}
    value = (value or "").strip().lower()
    return value if value in allowed else ""


def absorb(reader) -> list[Tag]:
    """Turn what this reader typed into interests. Returns the ones it touched.

    An interest already present is counted, not duplicated - that count is
    the whole point. Matching is on the slug, so "Silent discos" and
    "silent discos" are the same interest.
    """
    from recommendations import ai

    proposals = ai.propose_interests(reader)
    touched = []
    for row in proposals:
        name = (row.get("name") or "").strip()[:60]
        slug = slugify(name)[:70]
        if not name or not slug:
            continue

        tag = Tag.objects.filter(slug=slug).first()
        if tag is None:
            tag = Tag.objects.create(
                name=name, slug=slug,
                category=_valid_category(row.get("category")),
                origin=Tag.Origin.READER, times_requested=1)
        else:
            # An editor's interest stays an editor's interest; we only note
            # that someone asked for it again.
            Tag.objects.filter(pk=tag.pk).update(
                times_requested=tag.times_requested + 1)
            tag.refresh_from_db(fields=["times_requested"])
        touched.append(tag)

    if touched:
        # Their own words are a stronger signal than a checkbox, so what
        # they asked for is added to what they picked.
        reader.interest_tags.add(*touched)
    return touched


def run(reader) -> dict:
    """Everything AI does with a new signup: absorb what they typed, then
    read their taste into signals. Synchronous; `start` puts it on a thread.

    There is no button for the second part any more. It is part of signing
    up, and an editor sees the result on the user's row.
    """
    from recommendations import ai

    absorbed = absorb(reader)
    if absorbed:
        logger.info("Absorbed %d typed interest(s) from %s", len(absorbed), reader.email)
    interpreted = bool(ai.is_enabled("interpret_readers") and ai.apply_interpretation(reader))
    if interpreted:
        logger.info("Interpreted %s's taste at signup", reader.email)
    return {"absorbed": absorbed, "interpreted": interpreted}


def start(reader) -> None:
    """Run on a background thread. Never raises into the signup."""

    def work():
        try:
            run(reader)
        except Exception:
            logger.exception("Could not read the signup for %s", reader.email)
        finally:
            close_old_connections()

    threading.Thread(target=work, daemon=True, name="absorb-interests").start()
