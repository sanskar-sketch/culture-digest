import uuid

from django.db import models

from opportunities.models import Opportunity
from readers.models import Reader


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
        help_text="The editorial call, e.g. 'GO.' or 'I think you'll love this.'")

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
        for threshold, stars in self.FIT_BANDS:
            if self.score >= threshold:
                return stars
        return 1

    @property
    def fit_display(self) -> str:
        return "★" * self.fit_stars + "☆" * (5 - self.fit_stars)


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
