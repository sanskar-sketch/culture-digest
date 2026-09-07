"""Writing one reader's version of a campaign.

Same rules as the newsletter's rationales (see recommendations.ai): the
editor's voice, nothing invented, and never load-bearing - if AI is off,
out of time, or fails, the body goes out as the editor wrote it.
"""

from __future__ import annotations

import logging

from recommendations import ai

from .models import fill_placeholders

logger = logging.getLogger(__name__)

CAMPAIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {
            "type": "string",
            "description": "Two to five short paragraphs separated by blank lines. "
            "No greeting, no sign-off - the template adds those.",
        },
    },
    "required": ["body"],
    "additionalProperties": False,
}

CAMPAIGN_SYSTEM = f"""You are the editor of a personalised culture newsletter,
writing a one-off email to one reader about something specific.

{ai.UNTRUSTED_NOTE}

THE VOICE

Write as the editor, in the first person, with an opinion. You are writing
to one person you know something about, not broadcasting. Where the brief
connects to what they've told you - an interest they picked, something
they wrote in their own words, where they live - say so plainly and name
the signal. If it doesn't connect to anything, don't force it; a clear,
useful email is better than a strained personal one.

Good: "You said you'll travel for the right thing. This is in Margate, and
it's the right thing."
Bad: "As a lover of culture, you'll adore this exciting opportunity!"

Rules:
- Address them as "you". Never write their name, and do not add a greeting
  or a sign-off - the template puts those around your text.
- No hype. No "immerse yourself", no "don't miss", no exclamation marks.
- Two to five short paragraphs separated by blank lines. Shorter is better.
- Dry wit is welcome. Enthusiasm that isn't earned is not.

WHAT YOU MAY NOT DO

Every factual claim must come from the editor's brief or draft below.
Do not invent or embellish dates, times, prices, venues, names, running
times, capacity, or what happens. Never invent a review, a quote, a star
rating, or a publication's opinion. If the brief doesn't say, leave it out.
A reader may act on this email; an invented detail sends them to the wrong
place on the wrong day."""


def _profile(reader) -> str:
    lines = [
        f"Categories they follow: {getattr(reader, 'interest_categories', None) or 'no preference stated'}",
        f"Based in: {getattr(reader, 'location', '') or 'not stated'}",
    ]
    if getattr(reader, "pk", None):
        tags = ", ".join(reader.interest_tags.values_list("name", flat=True))
        lines.append(f"Interests they picked: {tags or 'none'}")
    if getattr(reader, "budget", ""):
        lines.append(f"Budget: {reader.get_budget_display()}")
    if getattr(reader, "travel_radius", ""):
        lines.append(f"How far they'll travel: {reader.get_travel_radius_display()}")
    if getattr(reader, "ai_taste_summary", ""):
        lines.append(f"Taste summary: {reader.ai_taste_summary}")
    return "\n".join(lines)


def write_campaign_body(campaign, reader, budget: "ai.TimeBudget | None" = None) -> str | None:
    """AI's version of the body for this reader, or None to use the editor's."""
    if budget is not None and budget.exhausted():
        logger.info("AI time budget spent (%.1fs) - sending %s the body as written",
                    budget.spent, reader.email)
        return None

    user = f"""The editor's brief (the only source of facts):
{campaign.brief.strip() or "(none - work from the draft below)"}

The editor's own draft (rewrite for this reader; keep every fact, add none):
{campaign.body.strip() or "(none)"}

<reader_input>
{_profile(reader)}

In their own words, things they've loved: {getattr(reader, 'loved_examples', '') or "(nothing written)"}
Things not for them: {getattr(reader, 'disliked_examples', '') or "(nothing written)"}
Interests they typed in themselves: {getattr(reader, 'other_interests', '') or "(none)"}
Anything else they told us: {getattr(reader, 'notes', '') or "(nothing written)"}
</reader_input>"""

    result = ai._call(CAMPAIGN_SYSTEM, user, CAMPAIGN_SCHEMA, "campaign_email",
                      max_tokens=1200, feature="write_campaigns")
    body = ((result or {}).get("body") or "").strip()
    return body or None


def compose(campaign, reader, budget: "ai.TimeBudget | None" = None) -> tuple[str, str, bool]:
    """Return (subject, body_text, personalised) for one reader."""
    subject = fill_placeholders(campaign.subject, reader)
    if campaign.personalise:
        body = write_campaign_body(campaign, reader, budget)
        if body:
            return subject, body, True
    return subject, fill_placeholders(campaign.body or campaign.brief, reader), False
