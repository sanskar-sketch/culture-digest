"""The MVP matching engine.

Deliberately simple and rule-based: it scores each published, in-window
opportunity against a reader's stated preferences (tags, budget, location,
mainstream/unusual and intimate/large-scale sliders) plus a lightweight
"learning" term derived from past feedback (More like this / Not for me /
Save / Booked).

This is the natural place to later plug in an AI-assisted scorer or
rationale-writer — the calling contract (`top_matches_for_reader` returns a
ranked list of `Match`, `build_rationale` turns one into reader-facing copy)
is designed to stay stable while the internals get smarter.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from opportunities.models import Opportunity
from readers.models import Reader

from .models import Recommendation

# Ordered cheapest to priciest, so we can compute a "distance" for partial credit.
PRICE_TIER_ORDER = ["free", "budget", "moderate", "premium", "splurge"]

BUDGET_TO_PRICE_TIER = {
    Reader.Budget.FREE_CHEAP: "budget",
    Reader.Budget.MODERATE: "moderate",
    Reader.Budget.TREAT: "premium",
    Reader.Budget.NO_LIMIT: "splurge",
}

# Whether a reader whose travel radius doesn't strictly match should still
# see out-of-area opportunities at all (with a score penalty), or be excluded.
TRAVEL_RADIUS_ALLOWS_MISMATCH = {
    Reader.TravelRadius.LOCAL_ONLY: False,
    Reader.TravelRadius.WITHIN_CITY: False,
    Reader.TravelRadius.REGIONAL: True,
    Reader.TravelRadius.ANYWHERE: True,
}

def feedback_weights_from(config) -> dict:
    return {
        Recommendation.Feedback.MORE_LIKE_THIS: config.feedback_more_like_this,
        Recommendation.Feedback.SAVE: config.feedback_saved,
        Recommendation.Feedback.BOOKED: config.feedback_booked,
        Recommendation.Feedback.NOT_FOR_ME: config.feedback_not_for_me,
    }


@dataclasses.dataclass
class Match:
    opportunity: Opportunity
    score: float
    reasons: list[str]


def _price_distance(reader: Reader, opportunity: Opportunity) -> int:
    if opportunity.price_tier == Opportunity.PriceTier.FREE:
        return 0
    if not reader.budget:
        # No stated budget preference - don't penalise on price at all.
        return 0
    target_tier = BUDGET_TO_PRICE_TIER[reader.budget]
    return abs(
        PRICE_TIER_ORDER.index(opportunity.price_tier) - PRICE_TIER_ORDER.index(target_tier)
    )


def _location_matches(reader: Reader, opportunity: Opportunity) -> bool:
    if opportunity.is_online or not reader.location:
        # No stated home location - don't exclude on location at all.
        return True
    return reader.location.strip().lower() == opportunity.location_area.strip().lower()


def _feedback_tag_weights(reader: Reader, config) -> dict[int, float]:
    """tag id -> signed weight learned from this reader's past feedback.

    Opportunities sharing tags with things they said 'more_like_this'/
    'save'/'booked' about score higher; ones sharing tags with 'not_for_me'
    picks score lower (and are dropped entirely if the signal is strong).
    This is the whole MVP "gets better over time" loop.
    """
    weights: dict[int, float] = {}
    by_feedback = feedback_weights_from(config)
    past = (
        Recommendation.objects.filter(issue__reader=reader)
        .exclude(feedback=Recommendation.Feedback.NONE)
        .select_related("opportunity")
        .prefetch_related("opportunity__tags")
    )
    for rec in past:
        weight = by_feedback.get(rec.feedback)
        if not weight:
            continue
        for tag in rec.opportunity.tags.all():
            weights[tag.id] = weights.get(tag.id, 0) + weight
    return weights


def score_opportunity(
    reader: Reader, opportunity: Opportunity, feedback_weights: dict[int, float],
    config=None,
) -> Match | None:
    """Score one opportunity for one reader, or return None to exclude it.

    All weights come from the editable SiteConfig, so an editor can tune
    what the newsletter favours without a deploy.
    """
    if config is None:
        from siteconfig.models import SiteConfig

        config = SiteConfig.load()

    location_ok = _location_matches(reader, opportunity)
    # No stated travel radius - default to permissive rather than excluding.
    allows_mismatch = TRAVEL_RADIUS_ALLOWS_MISMATCH.get(reader.travel_radius, True)
    if not location_ok and not allows_mismatch:
        return None

    price_distance = _price_distance(reader, opportunity)
    if price_distance > config.max_price_distance:
        return None

    reasons: list[str] = []
    score = 0.0

    opp_tags = list(opportunity.tags.all())
    reader_tag_ids = set(reader.interest_tags.values_list("id", flat=True))
    overlap = [t for t in opp_tags if t.id in reader_tag_ids]
    if overlap:
        score += config.weight_tag_overlap * len(overlap)
        reasons.append("shared interest in " + ", ".join(t.name for t in overlap[:3]))

    # Broad category affinity - a weaker signal than a specific tag match,
    # but it means a reader who only picked categories still gets sensible
    # picks. An empty list means no preference, so no bonus and no penalty.
    if reader.interest_categories and opportunity.category in reader.interest_categories:
        score += config.weight_category
        if not overlap:
            reasons.append(f"{opportunity.get_category_display().lower()} is one of their things")

    # Interests inferred by AI from their free text (recommendations.ai).
    # Deliberately weighted below an explicitly picked tag: they told us the
    # one, we guessed the other. A dislike we inferred is a penalty, not an
    # exclusion - only their own repeated feedback drops a cluster outright.
    inferred_ids = set(reader.ai_inferred_tags.values_list("id", flat=True))
    inferred_overlap = [t for t in opp_tags if t.id in inferred_ids and t.id not in reader_tag_ids]
    if inferred_overlap:
        score += config.weight_inferred_tag * len(inferred_overlap)
        if not overlap:
            reasons.append(
                "sounds like the things they described loving"
            )

    avoid_ids = set(reader.ai_avoid_tags.values_list("id", flat=True))
    if any(t.id in avoid_ids for t in opp_tags):
        score -= config.penalty_avoid_tag

    learned = sum(feedback_weights.get(t.id, 0) for t in opp_tags)
    if learned:
        if learned < config.dislike_drop_threshold:
            # They've told us they dislike this cluster before - drop it
            # rather than merely down-ranking it.
            return None
        score += learned
        if learned > 0:
            reasons.append("similar to things they've liked before")

    score -= config.penalty_price_step * price_distance
    if price_distance == 0:
        reasons.append("fits their usual budget")

    if reader.mainstream_preference is not None:
        mainstream_gap = abs(reader.mainstream_preference - opportunity.mainstream_to_unusual)
        score -= config.penalty_mainstream_gap * mainstream_gap
        if mainstream_gap <= 1:
            reasons.append("matches their mainstream/unusual taste")

    if reader.scale_preference is not None:
        scale_gap = abs(reader.scale_preference - opportunity.intimate_to_large_scale)
        score -= config.penalty_scale_gap * scale_gap
        if scale_gap <= 1:
            reasons.append("the right scale for them (intimate vs. large-scale)")

    if opportunity.critic_rating:
        score += float(opportunity.critic_rating) * config.weight_critic_rating
        if opportunity.critic_rating >= 4:
            reasons.append("critically well-reviewed")

    if not location_ok:
        score -= config.penalty_out_of_area
        reasons.append("a bit further afield, but worth the trip")
    elif opportunity.is_online:
        reasons.append("available online, wherever they are")
    else:
        reasons.append(f"local to {opportunity.location_area}")

    return Match(opportunity=opportunity, score=score, reasons=reasons)


def top_matches_for_reader(reader: Reader, limit: int | None = None,
                           pool=None) -> list[Match]:
    """The best events for this reader, from the catalogue or from `pool`.

    `pool` is the editor's shortlist - the events they chose on the send
    page. Within it the same rules apply: published, not ended, not on
    cooldown, scored and ranked for this reader. An editor choosing the
    pool narrows what can be sent; it never overrides who it suits.
    """
    from siteconfig.models import SiteConfig

    config = SiteConfig.load()
    limit = limit or config.recommendations_per_send
    today = timezone.localdate()
    cooldown_cutoff = timezone.now() - timedelta(days=config.cooldown_days)

    recently_recommended_ids = set(
        Recommendation.objects.filter(
            issue__reader=reader, created_at__gte=cooldown_cutoff
        ).values_list("opportunity_id", flat=True)
    )

    candidates = (
        Opportunity.objects.filter(status=Opportunity.Status.PUBLISHED)
        .exclude(id__in=recently_recommended_ids)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .prefetch_related("tags")
    )
    if pool is not None:
        candidates = candidates.filter(id__in=list(pool))

    feedback_weights = _feedback_tag_weights(reader, config)

    matches = [
        match
        for opportunity in candidates
        if (match := score_opportunity(reader, opportunity, feedback_weights, config)) is not None
    ]
    matches.sort(key=lambda m: m.score, reverse=True)

    top = matches[:limit]
    if reader.open_to_surprise and top and len(matches) > len(top):
        wildcard = _pick_wildcard(reader, matches[len(top):])
        if wildcard is not None:
            top = [*top[:-1], wildcard]

    return top


def _pick_wildcard(reader: Reader, remaining: list[Match]) -> Match | None:
    """Pick the best-scoring remaining match that shares none of the
    reader's stated interest tags - a genuine "outside your usual taste"
    surprise, for readers who opted into that."""
    reader_tag_ids = set(reader.interest_tags.values_list("id", flat=True))
    for match in remaining:
        opp_tag_ids = {t.id for t in match.opportunity.tags.all()}
        if not opp_tag_ids & reader_tag_ids:
            match.reasons = [*match.reasons, "a wildcard pick, since you're up for a surprise"]
            return match
    return None


def build_rationale(match: Match, reader: Reader | None = None, budget=None) -> tuple[str, str]:
    """Turn a Match into a short reader-facing 'why this suits you' blurb.

    Written by AI when a reader is given and AI is configured, so the line
    speaks to that person's actual taste. Falls back to the template below
    whenever AI is off or the call fails - a newsletter is never blocked on
    it, and the fallback is what shipped before.
    """
    if reader is not None:
        from . import ai

        written = ai.write_rationale(reader, match.opportunity, match.reasons, budget)
        if written:
            return written

    opportunity = match.opportunity
    base = (opportunity.editorial_note or opportunity.description).strip().split("\n")[0]
    reasons = match.reasons[:3]
    if reasons:
        return f"{base} We picked this because it's {'; '.join(reasons)}.", ""
    return base, ""
