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

from .emails import EmailTemplate  # noqa: F401  (registered via this module)

CACHE_KEY = "siteconfig"
CACHE_TTL = 300


class SiteConfig(models.Model):
    """Singleton. Always row 1 - `SiteConfig.load()` is the way in."""

    # --- Branding -------------------------------------------------------
    site_name = models.CharField(max_length=80, default="The Ether")
    tagline = models.CharField(
        max_length=200,
        default="the cultural world, filtered down to what's worth your time.",
        help_text="Shown in the site footer and at the foot of the newsletter.",
    )
    hero_eyebrow = models.CharField(
        max_length=120, default="Personalised. Weekly. Worth it.")
    hero_headline = models.CharField(
        max_length=200,
        default="The cultural world, filtered.",
        help_text="The line under the name in the hero. Keep it short - it sits at "
                  "display size.",
    )
    hero_subhead = models.TextField(
        default="Theatre, music, film, exhibitions, talks, food and the genuinely "
                "odd — narrowed each week to a handful chosen for your taste, your "
                "budget and the time you actually have.",
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
    send_welcome_email = models.BooleanField(
        default=True,
        help_text="Email a confirmation the moment someone signs up, showing back "
                  "what they told us and how to change it.",
    )
    welcome_subject = models.CharField(
        max_length=200, default="You're in - welcome to {site}",
        help_text="Subject for the welcome email. {site} is replaced with the site name.",
    )
    # --- Which template each email uses --------------------------------
    welcome_template = models.ForeignKey(
        "siteconfig.EmailTemplate", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", limit_choices_to={"kind": "welcome"},
        help_text="Leave empty to use the built-in welcome email.",
    )
    newsletter_template = models.ForeignKey(
        "siteconfig.EmailTemplate", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", limit_choices_to={"kind": "newsletter"},
        help_text="Leave empty to use the built-in newsletter.",
    )
    campaign_template = models.ForeignKey(
        "siteconfig.EmailTemplate", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", limit_choices_to={"kind": "campaign"},
        help_text="Leave empty to use the built-in campaign email.",
    )

    # --- When the newsletter goes out ----------------------------------
    class Frequency(models.TextChoices):
        MANUAL = "manual", "Only when I trigger it"
        WEEKLY = "weekly", "Weekly"
        FORTNIGHTLY = "fortnightly", "Every two weeks"
        MONTHLY = "monthly", "Monthly"

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    send_frequency = models.CharField(
        max_length=20, choices=Frequency.choices, default=Frequency.MANUAL,
        help_text="How often the newsletter goes out. The scheduler checks every "
                  "few minutes and sends on the day and hour set here.",
    )
    send_weekday = models.IntegerField(
        choices=Weekday.choices, default=Weekday.THURSDAY,
        help_text="Which day it goes out on, for weekly and fortnightly.")
    send_day_of_month = models.PositiveSmallIntegerField(
        default=1, help_text="Which date it goes out on, for monthly (1-28).")
    send_hour = models.PositiveSmallIntegerField(
        default=8,
        help_text="Hour of the day it goes out from, 0-23, in the site's time zone "
                  "(UTC unless changed in settings). The scheduler sends on its first "
                  "pass after this hour on a send day.")
    last_sent_on = models.DateField(
        null=True, blank=True,
        help_text="Set automatically after a scheduled run, so a cadence isn't "
                  "repeated if the trigger fires more than once a day.")

    subject_template = models.CharField(
        max_length=200,
        default="{name}{count} thing{plural} worth your time",
        help_text="Placeholders: {name} (their first name plus a comma, or empty), "
                  "{first_name} (bare), {count}, and {plural} (an 's' unless there is "
                  "exactly one pick - so 'thing{plural}' reads correctly either way).",
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
    ai_write_campaigns = models.BooleanField(
        default=True,
        help_text="Write each reader's version of a campaign email from your brief.")
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

    # Copy that ships as a model default, so an improved default can be
    # offered to an existing configuration rather than being invisible.
    RESETTABLE = (
        "site_name", "tagline", "hero_eyebrow", "hero_headline", "hero_subhead",
        "subject_template", "welcome_subject",
    )

    def drift_from_defaults(self) -> list[dict]:
        """Fields whose stored value differs from the one shipped in code.

        Defaults only apply when a row is created, so improving the wording
        in code leaves every existing deployment on the old text with nothing
        to indicate it. This makes that visible, and resettable.
        """
        drift = []
        for name in self.RESETTABLE:
            shipped = self._meta.get_field(name).get_default()
            current = getattr(self, name)
            if current != shipped:
                drift.append({
                    "field": name,
                    "label": self._meta.get_field(name).verbose_name,
                    "current": current,
                    "shipped": shipped,
                })
        return drift

    def reset_to_defaults(self, fields=None) -> list[str]:
        """Put the shipped wording back. Returns the fields that changed."""
        names = list(fields) if fields else [d["field"] for d in self.drift_from_defaults()]
        changed = []
        for name in names:
            if name not in self.RESETTABLE:
                continue
            shipped = self._meta.get_field(name).get_default()
            if getattr(self, name) != shipped:
                setattr(self, name, shipped)
                changed.append(name)
        if changed:
            self.save()
        return changed

    def is_send_day(self, today=None) -> tuple[bool, str]:
        """Should a scheduled run send today? Returns (yes/no, why).

        The trigger is expected to fire daily; this decides whether today is
        actually a send day, so the schedule lives here rather than in cron
        syntax an editor can't see or change.
        """
        from datetime import timedelta

        from django.utils import timezone

        today = today or timezone.localdate()

        if self.send_frequency == self.Frequency.MANUAL:
            return False, "Sending is set to manual."
        if self.last_sent_on == today:
            return False, f"Already sent today ({today})."

        if self.send_frequency == self.Frequency.WEEKLY:
            if today.weekday() != self.send_weekday:
                return False, f"Not the send day ({self.get_send_weekday_display()})."
            return True, "Weekly send day."

        if self.send_frequency == self.Frequency.FORTNIGHTLY:
            if today.weekday() != self.send_weekday:
                return False, f"Not the send day ({self.get_send_weekday_display()})."
            if self.last_sent_on and (today - self.last_sent_on) < timedelta(days=13):
                return False, f"Last send was {self.last_sent_on}; not two weeks yet."
            return True, "Fortnightly send day."

        if self.send_frequency == self.Frequency.MONTHLY:
            if today.day != self.send_day_of_month:
                return False, f"Not the send date ({self.send_day_of_month})."
            return True, "Monthly send date."

        return False, "Unrecognised frequency."

    def should_send_now(self, now=None) -> tuple[bool, str]:
        """Is this the moment for a scheduled newsletter run?

        The day check lives in `is_send_day`; this adds the hour, so a
        scheduler that fires every few minutes sends once, after the hour
        the editor chose, rather than at midnight.
        """
        from django.utils import timezone

        now = timezone.localtime(now or timezone.now())
        ok, why = self.is_send_day(now.date())
        if not ok:
            return False, why
        if now.hour < self.send_hour:
            return False, f"Send day, but not until {self.send_hour:02d}:00."
        return True, why

    @property
    def resolved_email_from(self) -> str:
        return self.email_from or settings.EMAIL_FROM

    @property
    def resolved_ai_model(self) -> str:
        return self.ai_model or settings.OPENAI_MODEL

    def ai_available(self, feature: str) -> bool:
        """Is a given AI feature switched on *and* usable?

        `feature` is one of write_rationales / interpret_readers /
        classify_opportunities / write_campaigns.
        """
        if not (self.ai_enabled and settings.OPENAI_API_KEY):
            return False
        return bool(getattr(self, f"ai_{feature}", False))
