import uuid

from django.db import models

from opportunities.models import Category, Tag


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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name or self.email

    def ranked_interests(self) -> list[dict]:
        """Everything they picked, in their own order.

        What they ranked comes first, numbered 1, 2, 3 as they placed it;
        anything picked but never ranked follows, A to Z, unnumbered. Each
        carries the exception they set for it, if any. Reads the prefetched
        rows when a list page has prefetched them, so it costs no queries
        per reader there.
        """
        prefs = list(self.interest_preferences.all())
        rows, ranked_tags = [], set()
        for position, pref in enumerate(sorted((p for p in prefs if p.rank is not None),
                                               key=lambda p: (p.rank, p.label)), start=1):
            rows.append({"position": position, "name": pref.label,
                         "exception": pref.summary() if pref.is_set else ""})
            if pref.tag_id:
                ranked_tags.add(pref.tag_id)
        unranked_prefs = {p.tag_id: p for p in prefs if p.rank is None and p.tag_id}
        for tag in sorted(self.interest_tags.all(), key=lambda t: t.name.lower()):
            if tag.pk in ranked_tags:
                continue
            pref = unranked_prefs.get(tag.pk)
            rows.append({"position": None, "name": tag.name,
                         "exception": pref.summary() if pref is not None and pref.is_set else ""})
        return rows


class InterestPreference(models.Model):
    """One reader's exception for one interest.

    Most people don't have a single budget or a single willingness to
    travel: someone will cross London for a gig and want the gallery to be
    twenty minutes away, or spend freely on theatre and nothing on food.
    A blank field here means "same as my usual", so a reader only fills in
    what actually differs.
    """

    reader = models.ForeignKey(
        Reader, on_delete=models.CASCADE, related_name="interest_preferences")
    # One of these: a specific interest, or a whole category. Someone who
    # only ever picked "music" and "theatre" should still be able to say
    # they'll cross town for the music and not for the theatre.
    tag = models.ForeignKey(
        Tag, on_delete=models.CASCADE, related_name="reader_preferences",
        null=True, blank=True)
    category = models.CharField(max_length=30, choices=Category.choices, blank=True)

    travel_radius = models.CharField(
        max_length=20, choices=Reader.TravelRadius.choices, blank=True,
        help_text="How far they'll go for this one. Blank = their usual.")
    budget = models.CharField(
        max_length=20, choices=Reader.Budget.choices, blank=True,
        help_text="What they'll spend on this one. Blank = their usual.")
    scale_preference = models.PositiveSmallIntegerField(
        choices=[(i, i) for i in range(1, 6)], null=True, blank=True,
        help_text="1 = intimate, 5 = large-scale, for this interest. Blank = their usual.")
    mainstream_preference = models.PositiveSmallIntegerField(
        choices=[(i, i) for i in range(1, 6)], null=True, blank=True,
        help_text="1 = mainstream, 5 = niche, for this interest. Blank = their usual.")
    availability = models.JSONField(
        default=list, blank=True,
        help_text="When they're free for this one, e.g. ['weekends']. Empty = their usual.")
    rank = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Where they put this among everything they picked: 1 = matters most.")
    love = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="How much it matters to them, 1-10: the size of its bubble at signup. "
                  "The rank is worked out from these.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    FIELDS = ("travel_radius", "budget", "scale_preference", "mainstream_preference",
              "availability")
    LOVE_WORDS = ((9, "loves it"), (7, "really likes it"), (4, "likes it"), (1, "a mild interest"))

    @property
    def love_words(self) -> str:
        if self.love is None:
            return ""
        return next(words for floor, words in self.LOVE_WORDS if self.love >= floor)

    class Meta:
        ordering = ["rank", "category", "tag__name"]
        constraints = [
            models.UniqueConstraint(fields=["reader", "tag"], name="one_preference_per_interest",
                                    condition=models.Q(tag__isnull=False)),
            models.UniqueConstraint(fields=["reader", "category"], name="one_preference_per_category",
                                    condition=~models.Q(category="")),
            models.CheckConstraint(
                name="preference_names_one_thing",
                condition=(models.Q(tag__isnull=False, category="")
                           | models.Q(tag__isnull=True) & ~models.Q(category="")),
            ),
        ]

    def __str__(self):
        return f"{self.reader}: {self.label}"

    @property
    def label(self) -> str:
        """'jazz', or 'music (all of it)' for a whole category."""
        if self.tag_id:
            return self.tag.name
        return f"{self.get_category_display().lower()} (all of it)"

    @property
    def is_set(self) -> bool:
        """Does this say anything? An empty row is the same as no row."""
        return any(getattr(self, field) not in (None, "", []) for field in self.FIELDS)

    def summary(self) -> str:
        """'anywhere in my city, happy to treat myself' - for editors and AI."""
        parts = []
        if self.travel_radius:
            parts.append(self.get_travel_radius_display().lower())
        if self.budget:
            parts.append(self.get_budget_display().lower())
        if self.scale_preference:
            parts.append(f"scale {self.scale_preference}/5 (1 intimate, 5 large)")
        if self.mainstream_preference:
            parts.append(f"taste {self.mainstream_preference}/5 (1 mainstream, 5 niche)")
        if self.availability:
            labels = dict(Reader.Availability.choices)
            parts.append("free " + ", ".join(labels.get(v, v).lower() for v in self.availability))
        return ", ".join(parts)
