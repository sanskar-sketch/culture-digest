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

    is_active = models.BooleanField(default=True, help_text="Unchecked = unsubscribed.")
    unsubscribe_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name or self.email
