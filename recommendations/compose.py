"""Composing one reader's culture week.

An issue used to be the top four scores in a list. A culture week is closer
to what a good editor who knows you would send: the handful they'd put at
the top, then what else is worth knowing section by section - live music,
new music, theatre, art, film, watching at home, books - then what to book
ahead, and a plan for the week.

This module decides *what* goes in and *where*. The words come from
`ai.write_issue`, which rates each pick for this reader and writes it up;
without AI every pick still goes out, in the template's plainer words.

Three rules shape the selection:

* **Timing is part of the recommendation.** Something closing this week
  says so; something on until next July says there's no rush; something
  that hasn't opened yet goes in Book ahead, not among this week's picks.
* **Nothing is padded.** A pick has to reach the FOR YOU floor (★★★ by
  default) to go in. A section with nothing good enough is left out.
* **What a reader saved comes back.** Pressing Save means "I want to go".
  While it's still on it returns - inside the cooldown - so it doesn't slip
  past them, and more urgently as it nears closing.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from opportunities.models import Category, Opportunity

from . import matching
from .models import NewsletterIssue, Recommendation

logger = logging.getLogger(__name__)

# key, emoji, heading - in the order they appear in the email.
SECTIONS = [
    ("top", "⭐", "The ones I'd put at the top"),
    ("saved", "🔁", "Still on from your list"),
    ("music", "🎵", "Live music"),
    ("listen", "🎧", "New music"),
    ("theatre", "🎭", "Theatre"),
    ("exhibition", "🎨", "Art & exhibitions"),
    ("film", "🎬", "Film"),
    ("watch", "📺", "Watch at home"),
    ("book", "📚", "Books"),
    ("talk", "🎤", "Talks & ideas"),
    ("food", "🍽️", "Food & drink"),
    ("things", "✨", "Things to do"),
    ("book_ahead", "🚨", "Book ahead"),
]
SECTION_ORDER = {key: i for i, (key, _, _) in enumerate(SECTIONS)}
SECTION_TITLES = {key: (emoji, title) for key, emoji, title in SECTIONS}

CATEGORY_SECTION = {
    Category.MUSIC: "music",
    Category.LISTEN: "listen",
    Category.THEATRE: "theatre",
    Category.EXHIBITION: "exhibition",
    Category.FILM: "film",
    Category.WATCH: "watch",
    Category.BOOK: "book",
    Category.TALK: "talk",
    Category.FOOD: "food",
}

# Score to FOR YOU stars, in halves. Absolute rather than relative to the
# week: ranking against a thin week would award five stars to the least bad
# thing on offer. The whole-star points match Recommendation.FIT_BANDS.
STAR_BANDS = ((6.0, 5.0), (5.0, 4.5), (4.0, 4.0), (3.25, 3.5), (2.5, 3.0),
              (1.75, 2.5), (1.0, 2.0), (0.5, 1.5))

# At most this many of the top picks from one category, so the top of the
# issue reads as a week rather than a jazz listing.
TOP_PER_CATEGORY = 2
SAVED_MAX = 3
# AI may move a rating this far from what the matching scored. It knows
# things the arithmetic doesn't; it doesn't get to overrule it outright.
AI_STAR_LEEWAY = 1.0


def stars_for_score(score: float) -> float:
    for threshold, stars in STAR_BANDS:
        if score >= threshold:
            return stars
    return 1.0


def round_half(value: float) -> float:
    return max(1.0, min(5.0, round(float(value) * 2) / 2))


def section_for(opportunity) -> str:
    return CATEGORY_SECTION.get(opportunity.category, "things")


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def issue_week(today: date | None = None) -> tuple[date, date]:
    """The seven days an issue covers, starting the day it is written."""
    today = today or timezone.localdate()
    return today, today + timedelta(days=6)


def day_label(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def week_label(start: date, end: date) -> str:
    """'Thursday 17–Wednesday 23 September', or across a month boundary
    'Sunday 30 August–Saturday 5 September'."""
    if (start.year, start.month) == (end.year, end.month):
        return f"{start:%A} {start.day}–{end:%A} {end.day} {end:%B}"
    return f"{start:%A} {start.day} {start:%B}–{end:%A} {end.day} {end:%B}"


def timing_for(opportunity, start: date, end: date, book_ahead_days: int):
    """(timing, label) for how this sits against the week, or None to leave it out.

    timing is one of: last_chance, one_night, new, on, release, book_ahead.
    """
    s, e = opportunity.start_date, opportunity.end_date

    if opportunity.is_release:
        if s is None:
            return "on", ""
        if s > end:
            return None  # not out yet; a release is news in its week, not before
        if s >= start:
            return "release", f"Out {day_label(s)}"
        if s >= start - timedelta(days=14):
            return "release", f"Just out ({day_label(s)})"
        return "on", ""

    if e is not None and e < start:
        return None
    if s is not None and s > end:
        if s <= start + timedelta(days=book_ahead_days):
            return "book_ahead", f"From {day_label(s)}" if (e and e != s) else day_label(s)
        return None
    if e is not None and start <= e <= end and (s is None or s < e):
        if e == start:
            return "last_chance", "Last chance: closes today"
        return "last_chance", f"Final week: closes {day_label(e)}"
    if s is not None and start <= s <= end:
        if e is None or e == s:
            return "one_night", day_label(s)
        return "new", f"Opens {day_label(s)}"
    if s is not None and start - timedelta(days=7) <= s < start:
        return "new", f"Just opened ({day_label(s)})" + (f", until {day_label(e)}" if e else "")
    if e is not None:
        if (e - start).days > 60:
            return "on", f"On until {e.day} {e:%B %Y}"
        return "on", f"Until {day_label(e)}"
    return "on", ""


# ---------------------------------------------------------------------------
# The composed issue
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Pick:
    opportunity: Opportunity
    score: float
    reasons: list[str]
    timing: str
    timing_label: str
    section: str
    stars: float
    saved: bool = False
    wildcard: bool = False
    no_overlap: bool = False
    is_top: bool = False
    position: int = 0
    hook: str = ""
    rationale: str = ""
    caveat: str = ""
    edited: bool = False
    token: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def event_id(self) -> int:
        return self.opportunity.pk


@dataclasses.dataclass
class ComposedIssue:
    reader: object
    week_start: date
    week_end: date
    picks: list[Pick]
    intro: str = ""
    programme: list = dataclasses.field(default_factory=list)
    closing: str = ""
    written_by_ai: bool = False
    ok: bool = True
    message: str = ""

    @property
    def week_label(self) -> str:
        return week_label(self.week_start, self.week_end)

    @property
    def top(self) -> list[Pick]:
        return [p for p in self.picks if p.is_top]


# ---------------------------------------------------------------------------
# Choosing
# ---------------------------------------------------------------------------

def gather(reader, week: tuple[date, date], config, pool=None) -> list[Pick]:
    """Everything that could go in this reader's week, scored and timed."""
    start, end = week
    cutoff = timezone.now() - timedelta(days=config.cooldown_days)
    past = Recommendation.objects.filter(issue__reader=reader)
    recent = set(past.filter(created_at__gte=cutoff).values_list("opportunity_id", flat=True))
    # Booked means done, Not for me means don't: neither comes back, ever.
    finished = set(past.filter(feedback__in=[Recommendation.Feedback.BOOKED,
                                             Recommendation.Feedback.NOT_FOR_ME])
                   .values_list("opportunity_id", flat=True))
    saved = (set(past.filter(feedback=Recommendation.Feedback.SAVE)
                 .values_list("opportunity_id", flat=True))
             if config.carry_forward_saved else set())

    candidates = (Opportunity.objects.filter(status=Opportunity.Status.PUBLISHED)
                  .filter(Q(end_date__isnull=True) | Q(end_date__gte=start))
                  .prefetch_related("tags", "reviews"))
    if pool is not None:
        candidates = candidates.filter(id__in=list(pool))

    weights = matching._feedback_tag_weights(reader, config)
    reader_tag_ids = set(reader.interest_tags.values_list("id", flat=True))
    floor = float(config.min_for_you_stars)
    picks = []
    for opportunity in candidates:
        if opportunity.pk in finished:
            continue
        is_saved = opportunity.pk in saved
        if opportunity.pk in recent and not is_saved:
            continue
        timing = timing_for(opportunity, start, end, config.book_ahead_days)
        if timing is None:
            continue
        match = matching.score_opportunity(reader, opportunity, weights, config)
        if match is None:
            continue
        stars = stars_for_score(match.score)
        overlap = {t.id for t in opportunity.tags.all()} & reader_tag_ids
        # A pick one star under the floor is still worth writing up: the
        # editor's reading may lift it. Two stars under, it can't get there.
        if not is_saved and stars < floor - AI_STAR_LEEWAY:
            continue
        picks.append(Pick(
            opportunity=opportunity, score=match.score, reasons=match.reasons,
            timing=timing[0], timing_label=timing[1],
            section="saved" if is_saved else ("book_ahead" if timing[0] == "book_ahead"
                                              else section_for(opportunity)),
            stars=stars, saved=is_saved, no_overlap=not overlap,
        ))
    return picks


def _rank_key(pick: Pick):
    start = pick.opportunity.start_date or date.max
    return (-pick.stars, -pick.score, start, pick.opportunity.title.lower())


def shortlist(picks: list[Pick], config, reader) -> list[Pick]:
    """What gets written up: within the caps, best first."""
    ranked = sorted(picks, key=_rank_key)
    chosen, per_section = [], Counter()
    limits = {"saved": SAVED_MAX, "book_ahead": config.book_ahead_max}
    total_cap = config.recommendations_per_send
    for pick in ranked:
        if len(chosen) >= total_cap:
            break
        # Up to two of a category can go to the top, which doesn't use up
        # its section below; `arrange` holds each section to its own cap.
        cap = limits.get(pick.section, config.max_per_section + TOP_PER_CATEGORY)
        if per_section[pick.section] >= cap:
            continue
        chosen.append(pick)
        per_section[pick.section] += 1

    # Someone who asked to be surprised gets one thing from outside the
    # interests they picked - the best of those, if any is worth it.
    if getattr(reader, "open_to_surprise", False):
        spare = [p for p in ranked if p.no_overlap and p.score > 0 and not p.saved
                 and p.section != "book_ahead"]
        if spare:
            wildcard = spare[0]
            wildcard.wildcard = True
            wildcard.reasons = [*wildcard.reasons, "a wildcard pick, since you're up for a surprise"]
            if wildcard not in chosen:
                if len(chosen) >= total_cap:
                    chosen.pop()  # the weakest regular pick makes room
                chosen.append(wildcard)
    return chosen


def arrange(picks: list[Pick], config, *, respect_floor: bool = True) -> list[Pick]:
    """Drop what fell below the floor, choose the top, and put it in order."""
    floor = float(config.min_for_you_stars)
    kept = [p for p in picks if p.saved or p.wildcard or not respect_floor or p.stars >= floor]

    for p in kept:
        p.is_top = False
    main = sorted([p for p in kept if p.section not in ("saved", "book_ahead")], key=_rank_key)
    top, per_category = [], Counter()
    for pick in main:
        if len(top) >= config.top_picks_count:
            break
        if pick.wildcard:
            continue
        if per_category[pick.section] >= TOP_PER_CATEGORY:
            continue
        pick.is_top = True
        top.append(pick)
        per_category[pick.section] += 1

    def order(p):
        group = "top" if p.is_top else p.section
        return (SECTION_ORDER.get(group, 99), *_rank_key(p))

    in_sections = Counter()
    capped = []
    for pick in sorted(kept, key=order):
        if not pick.is_top and pick.section not in ("saved", "book_ahead"):
            if in_sections[pick.section] >= config.max_per_section:
                continue
            in_sections[pick.section] += 1
        capped.append(pick)
    ordered = capped
    for i, pick in enumerate(ordered):
        pick.position = i
    return ordered


def compose(reader, *, pool=None, overrides=None, today: date | None = None,
            use_ai: bool = True) -> ComposedIssue:
    """Choose, rate, write and arrange one reader's culture week. Stores nothing."""
    from siteconfig.models import SiteConfig

    from . import ai

    config = SiteConfig.load()
    week = issue_week(today)
    issue = ComposedIssue(reader=reader, week_start=week[0], week_end=week[1], picks=[])

    candidates = shortlist(gather(reader, week, config, pool=pool), config, reader)
    if not candidates:
        issue.ok = False
        issue.message = "Only 0 picks reached the FOR YOU floor for them."
        return issue

    written = ai.write_issue(reader, issue, candidates) if use_ai else None
    if written:
        _apply_writing(issue, candidates, written)
        issue.written_by_ai = True
    else:
        for pick in candidates:
            pick.rationale, pick.hook = matching.build_rationale(
                matching.Match(pick.opportunity, pick.score, pick.reasons), None)

    apply_overrides(issue, candidates, overrides)
    issue.picks = arrange(candidates, config)

    if len(issue.picks) < config.min_recommendations:
        issue.ok = False
        issue.message = (f"Only {len(issue.picks)} pick{'' if len(issue.picks) == 1 else 's'} "
                         f"reached {stars_label(config.min_for_you_stars)} for them "
                         f"(needs {config.min_recommendations}).")
    else:
        issue.message = f"{len(issue.picks)} pick{'' if len(issue.picks) == 1 else 's'} this week."
    return issue


def stars_label(value) -> str:
    from opportunities.models import stars_text
    return stars_text(value) or "no stars"


def _apply_writing(issue: ComposedIssue, picks: list[Pick], written: dict) -> None:
    by_id = {p.event_id: p for p in picks}
    for row in written.get("picks") or []:
        pick = by_id.get(row.get("event_id"))
        if pick is None:
            continue
        what = (row.get("what_it_is") or "").strip()
        why = (row.get("why_for_you") or "").strip()
        pick.rationale = "\n\n".join(part for part in (what, why) if part)
        pick.hook = (row.get("hook") or "").strip()[:200]
        pick.caveat = (row.get("caveat") or "").strip()
        try:
            asked = float(row.get("for_you_stars"))
        except (TypeError, ValueError):
            asked = pick.stars
        low, high = pick.stars - AI_STAR_LEEWAY, pick.stars + AI_STAR_LEEWAY
        pick.stars = round_half(min(high, max(low, asked)))
    # Anything the model skipped keeps the template's words rather than none.
    for pick in picks:
        if not pick.rationale:
            pick.rationale, pick.hook = matching.build_rationale(
                matching.Match(pick.opportunity, pick.score, pick.reasons), None)
    issue.intro = (written.get("intro") or "").strip()
    issue.closing = (written.get("closing") or "").strip()
    issue.programme = [
        {"day": str(row.get("day", "")).strip()[:40], "plan": str(row.get("plan", "")).strip()}
        for row in (written.get("programme") or [])
        if str(row.get("plan", "")).strip()
    ][:8]


def apply_overrides(issue: ComposedIssue, picks: list[Pick], overrides) -> None:
    """An editor's words replace AI's, pick by pick. Keyed by event id."""
    if not overrides:
        return
    whole = overrides.get("_issue") or {}
    if "intro" in whole:
        issue.intro = whole["intro"]
    if "closing" in whole:
        issue.closing = whole["closing"]
    for pick in picks:
        edit = overrides.get(str(pick.event_id)) or {}
        if not (edit.get("rationale") or "").strip():
            continue
        pick.rationale = edit["rationale"].strip()
        pick.hook = (edit.get("verdict") or "").strip()
        if "caveat" in edit:
            pick.caveat = (edit.get("caveat") or "").strip()
        if edit.get("stars"):
            try:
                pick.stars = round_half(float(edit["stars"]))
            except (TypeError, ValueError):
                pass
        pick.edited = True


# ---------------------------------------------------------------------------
# Storing and restoring
# ---------------------------------------------------------------------------

def materialise(issue: ComposedIssue) -> NewsletterIssue:
    """Write a composed issue into the records, ready to send."""
    stored = NewsletterIssue.objects.create(
        reader=issue.reader, week_start=issue.week_start, week_end=issue.week_end,
        intro=issue.intro, programme=issue.programme, closing=issue.closing)
    for pick in issue.picks:
        Recommendation.objects.create(
            issue=stored, opportunity=pick.opportunity, score=pick.score,
            rationale=pick.rationale, verdict=pick.hook, caveat=pick.caveat,
            for_you_rating=Decimal(str(pick.stars)), section="top" if pick.is_top else pick.section,
            is_top=pick.is_top, timing=pick.timing, timing_label=pick.timing_label[:80],
            position=pick.position, feedback_token=uuid.UUID(pick.token))
    return stored


def to_json(issue: ComposedIssue) -> dict:
    return {
        "week": [issue.week_start.isoformat(), issue.week_end.isoformat()],
        "intro": issue.intro, "programme": issue.programme, "closing": issue.closing,
        "ok": issue.ok, "message": issue.message, "written_by_ai": issue.written_by_ai,
        "picks": [{
            "event_id": p.event_id, "score": p.score, "reasons": p.reasons,
            "timing": p.timing, "timing_label": p.timing_label, "section": p.section,
            "stars": p.stars, "saved": p.saved, "wildcard": p.wildcard, "is_top": p.is_top,
            "position": p.position, "hook": p.hook, "rationale": p.rationale,
            "caveat": p.caveat, "edited": p.edited, "token": p.token,
        } for p in issue.picks],
    }


def from_json(reader, data: dict) -> ComposedIssue:
    rows = data.get("picks") or []
    events = Opportunity.objects.prefetch_related("tags", "reviews").in_bulk(
        [r["event_id"] for r in rows])
    picks = []
    for row in rows:
        opportunity = events.get(row["event_id"])
        if opportunity is None:
            continue  # deleted since the draft was written
        picks.append(Pick(
            opportunity=opportunity, score=row.get("score", 0), reasons=row.get("reasons") or [],
            timing=row.get("timing", ""), timing_label=row.get("timing_label", ""),
            section=row.get("section", ""), stars=row.get("stars", 3.0),
            saved=row.get("saved", False), wildcard=row.get("wildcard", False),
            is_top=row.get("is_top", False), position=row.get("position", 0),
            hook=row.get("hook", ""), rationale=row.get("rationale", ""),
            caveat=row.get("caveat", ""), edited=row.get("edited", False),
            token=row.get("token") or str(uuid.uuid4())))
    start, end = (date.fromisoformat(d) for d in data["week"])
    return ComposedIssue(
        reader=reader, week_start=start, week_end=end, picks=sorted(picks, key=lambda p: p.position),
        intro=data.get("intro", ""), programme=data.get("programme") or [],
        closing=data.get("closing", ""), written_by_ai=data.get("written_by_ai", False),
        ok=data.get("ok", True), message=data.get("message", ""))
