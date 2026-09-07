"""Editor-editable configuration.

Everything that decides how the product behaves - branding copy, send
rules, the matching weights, and the AI toggles - lives here rather than
in environment variables, so an editor can tune it from the admin without
a redeploy.

Environment variables remain the *defaults* (see `defaults()`), so an
existing deployment keeps its current behaviour until someone changes
something deliberately.

Secrets deliberately stay in the environment. API keys don't belong in a
database that editors can read and that gets dumped into backups.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.core.validators import MinValueValidator
from django.db import models

CACHE_KEY = "siteconfig"
CACHE_TTL = 300


class SiteConfig(models.Model):
    """Singleton. Always row 1 - `SiteConfig.load()` is the way in."""

    # --- Branding -------------------------------------------------------
    site_name = models.CharField(max_length=80, default="The Ether")
    tagline = models.CharField(
        max_length=200,
        default="a handful of things worth your time, picked for you.",
        help_text="Shown in the site footer and at the foot of the newsletter.",
    )
    hero_eyebrow = models.CharField(max_length=120, default="Personalised. Weekly. Worth it.")
    hero_headline = models.CharField(max_length=200, default="Culture, curated just for you.")
    hero_subhead = models.TextField(
        default="A handful of things to see, hear, eat and do — matched to your "
                "taste, your budget, and your week. Not everyone's.",
    )

    # --- Sending --------------------------------------------------------
    recommendations_per_send = models.PositiveSmallIntegerField(
        default=4, validators=[MinValueValidator(1)],
        help_text="How many picks go in one newsletter. The brief calls for a "
                  "small number with high confidence, not volume.",
    )
    min_recommendations = models.PositiveSmallIntegerField(
        default=2, validators=[MinValueValidator(1)],
        help_text="Below this many strong matches, a reader is skipped entirely "
                  "rather than sent a padded-out issue.",
    )
    cooldown_days = models.PositiveSmallIntegerField(
        default=60,
        help_text="Don't recommend the same opportunity to a reader again within "
                  "this many days.",
    )
    email_from = models.CharField(
        max_length=200, blank=True,
        help_text='Sender for the newsletter, e.g. "The Ether &lt;hello@example.com&gt;". '
                  "Blank uses the EMAIL_FROM environment variable. Must be a verified "
                  "sender in SendGrid or sends are rejected.",
    )
    subject_template = models.CharField(
        max_length=200,
        default="{name}{count} things you'll probably love this week",
        help_text="Placeholders: {name} (ends with a comma and space, or empty) "
                  "and {count}.",
    )

    # --- Matching weights ----------------------------------------------
    # Positive numbers add to an opportunity's score, penalties subtract.
    weight_tag_overlap = models.FloatField(
        default=2.0, help_text="Per interest tag shared with the reader's own picks.")
    weight_category = models.FloatField(
        default=1.5, help_text="When the category is one the reader follows.")
    weight_inferred_tag = models.FloatField(
        default=0.9,
        help_text="Per tag AI inferred from their free text. Keep below the tag "
                  "overlap weight: they told us the one, we guessed the other.")
    penalty_avoid_tag = models.FloatField(
        default=2.0, help_text="Subtracted when a tag AI flagged as unwanted appears.")
    weight_critic_rating = models.FloatField(
        default=0.2, help_text="Multiplied by the critic rating out of 5.")
    penalty_price_step = models.FloatField(
        default=0.6, help_text="Per price tier away from the reader's budget.")
    max_price_distance = models.PositiveSmallIntegerField(
        default=2, help_text="Beyond this many tiers from their budget, exclude entirely.")
    penalty_mainstream_gap = models.FloatField(
        default=0.4, help_text="Per point of difference on the mainstream/unusual dial.")
    penalty_scale_gap = models.FloatField(
        default=0.3, help_text="Per point of difference on the intimate/large-scale dial.")
    penalty_out_of_area = models.FloatField(
        default=1.5, help_text="For an opportunity outside the reader's own area.")

    # --- Learning from feedback ----------------------------------------
    feedback_more_like_this = models.FloatField(default=1.0)
    feedback_saved = models.FloatField(default=0.6)
    feedback_booked = models.FloatField(default=1.2)
    feedback_not_for_me = models.FloatField(default=-1.0)
    dislike_drop_threshold = models.FloatField(
        default=-1.0,
        help_text="Once a reader's accumulated dislike for a tag cluster falls "
                  "below this, drop it entirely instead of just down-ranking.",
    )

    # --- AI -------------------------------------------------------------
    ai_enabled = models.BooleanField(
        default=True,
        help_text="Master switch. Off means everything uses the deterministic "
                  "templates. Needs OPENAI_API_KEY set regardless.",
    )
    ai_write_rationales = models.BooleanField(
        default=True, help_text="Write the 'why this suits you' line for each pick.")
    ai_interpret_readers = models.BooleanField(
        default=True, help_text="Read reader free text into taste signals.")
    ai_classify_opportunities = models.BooleanField(
        default=True, help_text="Suggest tags and attributes for new listings.")
    ai_model = models.CharField(
        max_length=80, blank=True,
        help_text="Blank uses the OPENAI_MODEL environment variable.")
    ai_timeout_seconds = models.FloatField(
        default=8.0,
        help_text="Ceiling on a single AI call. Sends can run inside a web "
                  "request, where an overrunning call gets the worker killed.")
    ai_send_budget_seconds = models.FloatField(
        default=15.0,
        help_text="Total AI time for one newsletter. Once spent, remaining picks "
                  "use the template.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site configuration"
        verbose_name_plural = "site configuration"

    def __str__(self):
        return f"{self.site_name} configuration"

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)
        cache.delete(CACHE_KEY)

    def delete(self, *args, **kwargs):
        """Deleting the configuration would take the whole product's behaviour
        with it, so it isn't allowed."""
        return

    @classmethod
    def load(cls) -> "SiteConfig":
        config = cache.get(CACHE_KEY)
        if config is None:
            config, _ = cls.objects.get_or_create(pk=1, defaults=cls.defaults())
            cache.set(CACHE_KEY, config, CACHE_TTL)
        return config

    @classmethod
    def defaults(cls) -> dict:
        """Seed the singleton from the environment, so an existing deployment
        keeps behaving exactly as it did before this model existed."""
        return {
            "recommendations_per_send": getattr(settings, "RECOMMENDATIONS_PER_SEND", 4),
            "cooldown_days": getattr(settings, "RECOMMENDATION_COOLDOWN_DAYS", 60),
            "ai_model": getattr(settings, "OPENAI_MODEL", ""),
            "ai_timeout_seconds": getattr(settings, "OPENAI_TIMEOUT_SECONDS", 8.0),
            "ai_send_budget_seconds": getattr(settings, "AI_SEND_BUDGET_SECONDS", 15.0),
        }

    # Convenience accessors that fall back to the environment ------------

    @property
    def resolved_email_from(self) -> str:
        return self.email_from or settings.EMAIL_FROM

    @property
    def resolved_ai_model(self) -> str:
        return self.ai_model or settings.OPENAI_MODEL

    def ai_available(self, feature: str) -> bool:
        """Is a given AI feature switched on *and* usable?

        `feature` is one of write_rationales / interpret_readers /
        classify_opportunities.
        """
        if not (self.ai_enabled and settings.OPENAI_API_KEY):
            return False
        return bool(getattr(self, f"ai_{feature}", False))
