"""Critic reviews: found by AI, confirmed against the page, or checked by hand.

The newsletter puts the critics' stars beside our own FOR YOU rating, and
the critics' half is only worth printing if it's real. So:

* AI searches for professional reviews and must return the review's URL;
* each one is then checked here, against the page itself - it has to be on
  that publication's own site, about this event, and if it carries a star
  rating the page has to show that rating;
* only a review that passes, or that an editor has verified, reaches a
  reader. Everything else waits on the event page with a note saying what
  the check found.

A paywalled page can't be read, so a Times or FT review will usually wait
for an editor. That's the right failure: an unchecked rating stays out.
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from decimal import Decimal
from urllib.parse import urlparse

from django.db import close_old_connections
from django.utils import timezone

from .models import CriticReview, Opportunity

logger = logging.getLogger(__name__)

# Publications whose ratings we print, and the sites they live on. A review
# claiming to be the Guardian's has to be on the Guardian's site.
PUBLICATIONS = {
    "The Guardian": ("theguardian.com",),
    "The Observer": ("theguardian.com", "observer.co.uk"),
    "Time Out": ("timeout.com",),
    "The Times": ("thetimes.com", "thetimes.co.uk"),
    "The Sunday Times": ("thetimes.com", "thetimes.co.uk"),
    "Financial Times": ("ft.com",),
    "Evening Standard": ("standard.co.uk",),
    "The Telegraph": ("telegraph.co.uk",),
    "The Independent": ("independent.co.uk",),
    "The Stage": ("thestage.co.uk",),
    "i": ("inews.co.uk",),
    "Financial Times Weekend": ("ft.com",),
    "London Review of Books": ("lrb.co.uk",),
    "Pitchfork": ("pitchfork.com",),
    "NME": ("nme.com",),
    "Mojo": ("mojo4music.com",),
    "Uncut": ("uncut.co.uk",),
    "Empire": ("empireonline.com",),
    "Sight and Sound": ("bfi.org.uk",),
    "WhatsOnStage": ("whatsonstage.com",),
    "Broadway World": ("broadwayworld.com",),
}

_ALIASES = {
    "guardian": "The Guardian", "the guardian": "The Guardian",
    "observer": "The Observer", "the observer": "The Observer",
    "timeout": "Time Out", "time out london": "Time Out", "time out": "Time Out",
    "times": "The Times", "the times": "The Times", "sunday times": "The Sunday Times",
    "ft": "Financial Times", "financial times": "Financial Times",
    "evening standard": "Evening Standard", "the standard": "Evening Standard",
    "telegraph": "The Telegraph", "the telegraph": "The Telegraph",
    "daily telegraph": "The Telegraph",
    "independent": "The Independent", "the independent": "The Independent",
    "the stage": "The Stage", "stage": "The Stage",
}

USER_AGENT = ("Mozilla/5.0 (compatible; TheEtherReviewCheck/1.0; "
              "+https://culture-digest.onrender.com)")


def canonical_publication(name: str) -> str:
    name = (name or "").strip()
    return _ALIASES.get(name.lower(), name)


def domain_matches(publication: str, url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    domains = PUBLICATIONS.get(publication)
    if not domains:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


# ---------------------------------------------------------------------------
# Reading the page
# ---------------------------------------------------------------------------

def fetch(url: str, timeout: float = 12.0) -> tuple[int, str]:
    """(status, text). Never raises: a page we can't read is status 0."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - http(s) only, checked by the caller
            body = response.read(2_000_000).decode("utf-8", errors="replace")
            return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:  # timeouts, DNS, TLS
        logger.info("Could not fetch %s: %s", url, exc)
        return 0, ""


_JSONLD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
# "4 out of 5 stars", or "Rating: 4/5". A bare "2/5" is too often a date.
_OUT_OF_FIVE = re.compile(r'\b([0-5](?:\.5)?)\s*out of\s*5\s*stars?\b', re.I)
_RATING_SLASH = re.compile(r'rating[^0-9<]{0,20}([0-5](?:\.5)?)\s*(?:/|out of)\s*5\b', re.I)
_STAR_GLYPHS = re.compile(r'(★{1,5})(☆{0,4})')


def _ratings_in_jsonld(page: str) -> list[float]:
    found = []

    def walk(node):
        if isinstance(node, dict):
            rating = node.get("reviewRating")
            if isinstance(rating, dict):
                try:
                    best = float(rating.get("bestRating") or 5)
                    value = float(rating.get("ratingValue"))
                    if best > 0:
                        found.append(round(value / best * 5 * 2) / 2)
                except (TypeError, ValueError):
                    pass
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for block in _JSONLD.findall(page):
        try:
            walk(json.loads(html.unescape(block.strip())))
        except ValueError:
            continue
    return found


def ratings_on_page(page: str) -> list[float]:
    """Every star rating the page states, most trustworthy source first."""
    ratings = _ratings_in_jsonld(page)
    text = re.sub(r"<[^>]+>", " ", page)
    for pattern in (_OUT_OF_FIVE, _RATING_SLASH):
        for match in pattern.finditer(text):
            ratings.append(float(match.group(1)))
    for full, empty in _STAR_GLYPHS.findall(text):
        if len(full) + len(empty) == 5:
            ratings.append(float(len(full)))
    return ratings


def about_this(page: str, opportunity: Opportunity) -> bool:
    """Does the page talk about this event at all? Most of its title's words."""
    text = re.sub(r"<[^>]+>", " ", html.unescape(page)).lower()
    words = [w for w in re.findall(r"[a-z0-9']{4,}", opportunity.title.lower())
             if w not in {"with", "from", "that", "this", "live", "tour", "presents"}]
    if not words:
        return True
    hits = sum(1 for w in set(words) if w in text)
    return hits >= max(1, round(len(set(words)) * 0.6))


def check(review: CriticReview, fetcher=fetch) -> CriticReview:
    """Confirm a review against its page. Sets verified, stars and a note."""
    review.checked_at = timezone.now()
    if review.verified == CriticReview.Verified.EDITOR:
        return review  # a person's check outranks ours
    review.verified = ""
    if not review.url.startswith(("http://", "https://")):
        review.check_note = "Not a web address."
        return review
    if not domain_matches(review.publication, review.url):
        review.check_note = (f"The link isn't on {review.publication}'s own site"
                             if review.publication in PUBLICATIONS
                             else f"We don't know {review.publication}'s site - check it by hand.")
        return review

    status, page = fetcher(review.url)
    if status != 200 or not page:
        review.check_note = (f"Couldn't read the page (HTTP {status}) - often a paywall. "
                             "Check it by hand." if status else
                             "Couldn't reach the page. Check it by hand.")
        return review
    if not about_this(page, review.opportunity):
        review.check_note = "The page doesn't seem to be about this event."
        return review

    ratings = ratings_on_page(page)
    if ratings:
        stated = ratings[0]
        if review.stars is not None and float(review.stars) != stated:
            review.check_note = f"AI said {float(review.stars):g} stars; the page says {stated:g}."
        else:
            review.check_note = f"The page shows {stated:g} stars."
        review.stars = Decimal(str(stated))
        review.verified = CriticReview.Verified.PAGE
        return review

    if review.stars is not None:
        review.check_note = (f"AI said {float(review.stars):g} stars but the page shows no rating "
                             "we can read. Check it by hand.")
        return review
    review.check_note = ("The review is real but no star rating was found on the page. "
                         "Verify it if it genuinely has none.")
    return review


# ---------------------------------------------------------------------------
# Finding them
# ---------------------------------------------------------------------------

def save_found(opportunity: Opportunity, found: list[dict], fetcher=fetch) -> list[CriticReview]:
    """Store and check what the search returned. Returns the reviews created."""
    created = []
    have = set(opportunity.reviews.values_list("publication", flat=True))
    for row in found:
        publication = canonical_publication(row.get("publication", ""))[:80]
        url = (row.get("url") or "").strip()
        if not publication or not url.startswith("http") or publication in have:
            continue
        stars = row.get("stars")
        try:
            stars = None if stars in (None, "", 0) else Decimal(str(round(float(stars) * 2) / 2))
        except (TypeError, ValueError):
            stars = None
        review = CriticReview(opportunity=opportunity, publication=publication, stars=stars,
                              url=url[:500], quote=(row.get("quote") or "").strip()[:300],
                              found_by_ai=True)
        check(review, fetcher=fetcher)
        review.save()
        have.add(publication)
        created.append(review)
    return created


def find(opportunity: Opportunity) -> dict:
    """Search for reviews of one event and store what checks out. Synchronous."""
    from recommendations import ai

    payload = ai.research_reviews(opportunity)
    if payload is None:
        return {"ok": False, "created": []}
    created = save_found(opportunity, payload.get("reviews") or [])
    return {"ok": True, "created": created, "notes": payload.get("notes", "")}


_running: set[int] = set()
_lock = threading.Lock()


def start(opportunity: Opportunity) -> bool:
    """Search in the background. False if AI is off or a search is already running."""
    from recommendations import ai

    if not ai.is_enabled("classify_opportunities"):
        return False
    with _lock:
        if opportunity.pk in _running:
            return False
        _running.add(opportunity.pk)

    def work():
        try:
            result = find(opportunity)
            logger.info("Review search for %r stored %d", opportunity.title, len(result["created"]))
        except Exception:
            logger.exception("Review search failed for %r", opportunity.title)
        finally:
            close_old_connections()
            with _lock:
                _running.discard(opportunity.pk)

    threading.Thread(target=work, daemon=True, name=f"reviews-{opportunity.pk}").start()
    return True


def is_running(opportunity: Opportunity) -> bool:
    with _lock:
        return opportunity.pk in _running


def start_many(opportunities) -> bool:
    """Search for several events one after another, on one thread.

    Accepting a page of finds shouldn't open ten web searches at once.
    """
    from recommendations import ai

    if not ai.is_enabled("classify_opportunities"):
        return False
    todo = [o for o in opportunities if not o.reviews.exists()]
    if not todo:
        return False

    def work():
        try:
            for opportunity in todo:
                with _lock:
                    if opportunity.pk in _running:
                        continue
                    _running.add(opportunity.pk)
                try:
                    find(opportunity)
                except Exception:
                    logger.exception("Review search failed for %r", opportunity.title)
                finally:
                    with _lock:
                        _running.discard(opportunity.pk)
        finally:
            close_old_connections()

    threading.Thread(target=work, daemon=True, name="reviews-batch").start()
    return True
