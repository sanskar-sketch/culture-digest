from django.conf import settings
from django.db import models
from django.utils.text import slugify


class Category(models.TextChoices):
    """The broad kinds of cultural opportunity the newsletter covers.

    Defined at module level so both Tag and Opportunity can reference it;
    `Opportunity.Category` stays available as an alias.
    """

    THEATRE = "theatre", "Theatre"
    MUSIC = "music", "Music"
    FILM = "film", "Film"
    EXHIBITION = "exhibition", "Exhibition"
    TALK = "talk", "Talk / lecture"
    FOOD = "food", "Food & drink"
    EVENT = "event", "Event"
    UNUSUAL = "unusual", "Unusual experience"
    OTHER = "other", "Other"


class Tag(models.Model):
    """A free-form taste/interest tag shared between readers and opportunities.

    Readers pick tags that describe their interests during onboarding;
    opportunities are tagged editorially. Overlap between the two is one of
    the core matching signals.
    """

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=70, unique=True, blank=True)
    category = models.CharField(
        max_length=20, choices=Category.choices, blank=True,
        help_text="Which broad category this interest sits under. Drives which "
        "tags a reader is shown during onboarding. Blank = always shown.",
    )

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Opportunity(models.Model):
    """A single curated cultural opportunity: a show, exhibition, meal, talk,
    or unusual experience that could be recommended to a reader.

    Curation and tagging is expected to be largely editorial (via the admin)
    in the MVP, with AI assisting research, classification and rationale
    drafting rather than replacing the editorial judgement call.
    """

    Category = Category

    class PriceTier(models.TextChoices):
        FREE = "free", "Free"
        BUDGET = "budget", "Budget (under ~£20)"
        MODERATE = "moderate", "Moderate (~£20-£50)"
        PREMIUM = "premium", "Premium (~£50-£100)"
        SPLURGE = "splurge", "Splurge (£100+)"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.TextField(
        help_text="Reader-facing description of the opportunity."
    )
    editorial_note = models.TextField(
        blank=True,
        help_text="Internal notes on why this is worth recommending and to whom. "
        "Used as raw material for the recommendation rationale.",
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="opportunities")

    # Practical attributes
    price_tier = models.CharField(max_length=20, choices=PriceTier.choices)
    price_display = models.CharField(
        max_length=60, blank=True, help_text="Human-readable price, e.g. '£25-£45'."
    )
    location_name = models.CharField(max_length=200, blank=True, help_text="Venue name.")
    location_area = models.CharField(
        max_length=120,
        help_text="City or area used for matching against a reader's location, e.g. 'London'.",
    )
    is_online = models.BooleanField(
        default=False, help_text="Doesn't require travel, e.g. a livestream or film to watch at home."
    )
    booking_url = models.URLField(max_length=500)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(
        null=True, blank=True, help_text="Leave blank for ongoing / no fixed end date."
    )

    # Taste attributes
    critic_rating = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True,
        help_text="Rating out of 5, if available.",
    )
    critic_rating_source = models.CharField(
        max_length=120, blank=True, help_text="e.g. 'The Guardian, 4/5'."
    )
    critic_quote = models.TextField(
        blank=True,
        help_text="A short quote from an actual review, if you have one. Only ever "
                  "paste something real - the newsletter attributes it by name, and "
                  "nothing will invent one for you.",
    )
    mainstream_to_unusual = models.PositiveSmallIntegerField(
        choices=[(i, i) for i in range(1, 6)],
        help_text="1 = mainstream/crowd-pleasing, 5 = niche/unusual.",
    )
    intimate_to_large_scale = models.PositiveSmallIntegerField(
        choices=[(i, i) for i in range(1, 6)],
        help_text="1 = intimate/small-scale, 5 = big/large-scale.",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "opportunities"

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:220]
        super().save(*args, **kwargs)
