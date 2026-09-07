import uuid

from django.db import models

from opportunities.models import Tag


class Reader(models.Model):
    """A newsletter subscriber and their onboarding profile.

    Populated once from the onboarding questionnaire, and refined over time
    implicitly by the feedback they give on recommendations
    (see recommendations.Recommendation.feedback).
    """

    class TravelRadius(models.TextChoices):
        LOCAL_ONLY = "local_only", "Only things close by"
        WITHIN_CITY = "within_city", "Anywhere in my city"
        REGIONAL = "regional", "I'll travel regionally for the right thing"
        ANYWHERE = "anywhere", "I'll travel far for something special"

    class Budget(models.TextChoices):
        FREE_CHEAP = "free_cheap", "Free / very cheap"
        MODERATE = "moderate", "Moderate"
        TREAT = "treat", "Happy to treat myself"
        NO_LIMIT = "no_limit", "Budget isn't a constraint"

    class Availability(models.TextChoices):
        WEEKDAY_DAYTIME = "weekday_daytime", "Weekday daytime"
        WEEKDAY_EVENINGS = "weekday_evenings", "Weekday evenings"
        WEEKENDS = "weekends", "Weekends"
        FLEXIBLE = "flexible", "Flexible / varies"

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=120, blank=True)
    age = models.PositiveSmallIntegerField(null=True, blank=True)

    interest_categories = models.JSONField(
        default=list, blank=True,
        help_text="Broad categories picked during onboarding, e.g. ['music', 'theatre']. "
        "Also decides which interest tags they're offered.",
    )
    interest_tags = models.ManyToManyField(
        Tag, blank=True, related_name="interested_readers",
        help_text="Interests picked during onboarding.",
    )
    loved_examples = models.TextField(
        blank=True,
        help_text="Free text: things this reader has loved (shows, exhibitions, meals, etc).",
    )
    disliked_examples = models.TextField(
        blank=True, help_text="Free text: things this reader hasn't enjoyed."
    )
    # Write-ins. Every choice question offers "Other", because a fixed list
    # can only ever cover what we thought of - and the gaps are exactly where
    # someone's real taste tends to live.
    other_categories = models.TextField(
        blank=True, help_text="Categories the reader typed in themselves.")
    other_interests = models.TextField(
        blank=True, help_text="Interests not in the tag taxonomy, in their words.")
    other_travel = models.TextField(
        blank=True, help_text="How far they'll travel, in their own words.")
    other_budget = models.TextField(
        blank=True, help_text="Their budget in their own words, e.g. ranges or exceptions.")
    other_availability = models.TextField(
        blank=True, help_text="When they're free, in their own words.")

    notes = models.TextField(
        blank=True,
        help_text="Anything the reader wanted to tell us, in their own words. "
        "Open-ended, so it's the place they can say things the rest of the "
        "questionnaire has no field for. Fed into matching via AI "
        "interpretation, and into the wording of their recommendations.",
    )

    location = models.CharField(
        max_length=120, blank=True, help_text="Where they live - city or area, e.g. 'London'."
    )
    travel_destinations = models.TextField(
        blank=True,
        help_text="Free text: places they love to travel to, or would love to visit.",
    )
    travel_radius = models.CharField(max_length=20, choices=TravelRadius.choices, blank=True)
    budget = models.CharField(max_length=20, choices=Budget.choices, blank=True)
    availability = models.JSONField(
        default=list, blank=True,
        help_text="List of Availability values, e.g. ['weekends', 'weekday_evenings'].",
    )

    mainstream_preference = models.PositiveSmallIntegerField(
        choices=[(i, i) for i in range(1, 6)], null=True, blank=True,
        help_text="1 = strongly prefers mainstream/crowd-pleasing, 5 = strongly prefers niche/unusual. "
        "Blank = no stated preference.",
    )
    scale_preference = models.PositiveSmallIntegerField(
        choices=[(i, i) for i in range(1, 6)], null=True, blank=True,
        help_text="1 = prefers intimate/small-scale, 5 = prefers big/large-scale. "
        "Blank = no stated preference.",
    )
    open_to_surprise = models.BooleanField(
        default=False,
        help_text="Happy to occasionally get a wildcard pick outside their usual taste.",
    )

    # Derived by AI from the free-text answers (see recommendations.ai).
    # Kept separate from what the reader explicitly picked, so the two are
    # never confused and inferred signals can be weighted lower.
    ai_taste_summary = models.TextField(
        blank=True,
        help_text="AI reading of this reader's free text, for editors and for "
        "writing their recommendations.",
    )
    ai_inferred_tags = models.ManyToManyField(
        Tag, blank=True, related_name="ai_inferred_readers",
        help_text="Interests inferred from free text, not explicitly picked.",
    )
    ai_avoid_tags = models.ManyToManyField(
        Tag, blank=True, related_name="ai_avoiding_readers",
        help_text="Things they signalled they don't want, inferred from free text.",
    )
    ai_profile_updated_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True, help_text="Unchecked = unsubscribed.")
    unsubscribe_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # Separate from the unsubscribe token so either can be revoked alone.
    edit_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name or self.email
