import uuid

from django.conf import settings
from django.db import models
from django.db.models import F

from opportunities.models import Opportunity, stars_text
from readers.models import Reader


# The order picks are read in, wherever a newsletter is shown or sent: the
# order the issue was composed in (top picks, then each section), and for
# issues from before sections, the best match first and the soonest event
# first when two score the same. The model's default order is newest-first,
# which for one issue meant the weakest pick was numbered 1.
PICK_ORDER = ("position", "-score", F("opportunity__start_date").asc(nulls_last=True),
              "created_at")


class NewsletterIssue(models.Model):
    """One personalised newsletter send to one reader, containing a small
    handful of Recommendations."""

    reader = models.ForeignKey(Reader, on_delete=models.CASCADE, related_name="issues")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(
        max_length=120, blank=True,
        help_text="Message id returned by the email provider, for tracing a send.",
    )

    # A culture week: the seven days it covers, the editor's note that opens
    # it, a day-by-day plan through it, and the line that closes it.
    week_start = models.DateField(null=True, blank=True)
    week_end = models.DateField(null=True, blank=True)
    intro = models.TextField(blank=True, help_text="The editor's note at the top.")
    programme = models.JSONField(
        default=list, blank=True,
        help_text="If I were programming your week: [{day, plan}].")
    section_notes = models.JSONField(
        default=dict, blank=True,
        help_text="A line under a section heading, by section key: 'There isn't a big album this week…'")
    strongest = models.JSONField(
        default=list, blank=True, help_text="Event ids of the strongest bets, best first.")
    closing = models.TextField(blank=True, help_text="The closing line: the strongest bets.")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Issue for {self.reader} ({self.created_at:%Y-%m-%d})"


class Recommendation(models.Model):
    """A single opportunity recommended to a reader as part of one
    NewsletterIssue, plus whatever feedback they gave on it."""

    class Feedback(models.TextChoices):
        NONE = "none", "No feedback yet"
        MORE_LIKE_THIS = "more_like_this", "More like this"
        NOT_FOR_ME = "not_for_me", "Not for me"
        SAVE = "save", "Save"
        BOOKED = "booked", "Booked"

    issue = models.ForeignKey(
        NewsletterIssue, on_delete=models.CASCADE, related_name="recommendations"
    )
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.CASCADE, related_name="recommendations"
    )
    rationale = models.TextField(help_text="Short reader-facing explanation of why this fits.")
    score = models.FloatField(default=0, help_text="Internal matching score, for debugging/tuning.")
    verdict = models.CharField(
        max_length=200, blank=True,
        help_text="The opening line: the editor's call on it, e.g. 'This is probably "
                  "my best live-music bet for you this week.'")
    caveat = models.TextField(
        blank=True, help_text="Anything worth knowing before going: length, sold-out "
                              "runs, mixed reviews. Empty when there is nothing.")
    for_you_rating = models.DecimalField(
        max_digits=2, decimal_places=1, null=True, blank=True,
        help_text="FOR YOU, out of 5 with halves: how strongly this reader should consider it.")
    section = models.CharField(max_length=20, blank=True,
                               help_text="Where in the issue it sits: top, a category, book ahead.")
    is_top = models.BooleanField(default=False, help_text="One of the picks at the top of the issue.")
    timing = models.CharField(max_length=20, blank=True,
                              help_text="last_chance, new, one_night, on, release, book_ahead, saved.")
    timing_label = models.CharField(max_length=80, blank=True,
                                    help_text="As printed, e.g. 'Closes Sat 19 Sep'.")
    position = models.PositiveSmallIntegerField(default=0)

    feedback = models.CharField(
        max_length=20, choices=Feedback.choices, default=Feedback.NONE
    )
    feedback_at = models.DateTimeField(null=True, blank=True)
    feedback_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.opportunity} -> {self.issue.reader}"

    @property
    def reader(self):
        return self.issue.reader

    # Score bands for the reader-facing fit rating. Absolute rather than
    # relative to the issue: scoring against the rest of a weak week would
    # award five stars to the least bad thing available.
    FIT_BANDS = ((6.0, 5), (4.0, 4), (2.5, 3), (1.0, 2))

    @property
    def fit_stars(self) -> int:
        if self.for_you_rating is not None:
            return int(float(self.for_you_rating))
        for threshold, stars in self.FIT_BANDS:
            if self.score >= threshold:
                return stars
        return 1

    @property
    def rating(self) -> float:
        """FOR YOU out of 5: the rating given, or the score's own band."""
        if self.for_you_rating is not None:
            return float(self.for_you_rating)
        return float(self.fit_stars)

    @property
    def fit_display(self) -> str:
        return stars_text(self.rating)


class LinkClick(models.Model):
    """One reader following one link out of one email.

    The feedback buttons already say what a reader thought. This says what
    they actually did - which pick they opened, and which part of the email
    they used - which is the difference between "they liked the idea" and
    "they went".

    Deliberately thin: who, what, where in the email, when. No IP, no user
    agent, nothing that turns a newsletter into surveillance.
    """

    class Section(models.TextChoices):
        BOOKING = "booking", "Booking link on a pick"
        REVIEW = "review", "Critic review link on a pick"
        FEEDBACK = "feedback", "Feedback button"
        CAMPAIGN = "campaign", "Campaign button"
        OTHER = "other", "Something else"

    reader = models.ForeignKey("readers.Reader", on_delete=models.CASCADE,
                               related_name="link_clicks")
    recommendation = models.ForeignKey(
        "recommendations.Recommendation", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="link_clicks")
    campaign = models.ForeignKey(
        "campaigns.Campaign", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="link_clicks")
    section = models.CharField(max_length=20, choices=Section.choices,
                               default=Section.OTHER)
    url = models.TextField()
    clicked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-clicked_at"]
        indexes = [models.Index(fields=["section", "clicked_at"])]

    def __str__(self):
        return f"{self.reader.email} → {self.get_section_display()}"


class ReaderReply(models.Model):
    """Something a reader told us in words.

    Four buttons say whether a pick landed; they can't say why. A line like
    "tribute nights aren't for me, I want the actual artists" changes every
    issue after it, and the only way to get it is to ask and keep it.
    """

    reader = models.ForeignKey("readers.Reader", on_delete=models.CASCADE, related_name="replies")
    issue = models.ForeignKey(NewsletterIssue, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="replies")
    recommendation = models.ForeignKey(Recommendation, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="replies")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "reader replies"

    def __str__(self):
        return f"{self.reader.email}: {self.text[:40]}"


class IssueDraft(models.Model):
    """A reader's next issue, written but not yet sent.

    Writing a whole culture week takes AI the better part of a minute, far
    longer than a web request may live, so the desk builds it here in the
    background, shows it, lets the editor change it, and sends exactly what
    was shown. Separate from NewsletterIssue so an unsent draft is never
    counted, never puts anything on cooldown and never appears in records.
    """

    class Status(models.TextChoices):
        BUILDING = "building", "Writing"
        READY = "ready", "Ready"
        EMPTY = "empty", "Nothing to send"
        FAILED = "failed", "Failed"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"

    reader = models.ForeignKey("readers.Reader", on_delete=models.CASCADE, related_name="drafts")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BUILDING)
    message = models.TextField(blank=True)
    pool = models.JSONField(null=True, blank=True,
                            help_text="The event ids it was built from, or null for everything.")
    content = models.JSONField(default=dict, blank=True)
    send_when_ready = models.BooleanField(default=False)
    issue = models.ForeignKey(NewsletterIssue, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Draft for {self.reader.email} ({self.get_status_display()})"
