"""One-off emails on any subject, to everyone or a slice of readers.

The newsletter is built from the catalogue. A campaign is built from a
brief: the editor says what it's about, who should get it and when, and
each reader still gets their own version - written by AI from the brief
and their profile, in the newsletter's voice - unless personalisation is
switched off, in which case the body goes out as written.

Sending is resumable. Every reader gets a `CampaignDelivery` row up front,
and a run works through the pending ones until its time budget is spent.
The scheduler picks up where it left off, so a large audience never has
to fit inside one web request.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

from opportunities.models import Tag
from readers.models import Reader


def fill_placeholders(text: str, reader) -> str:
    """{first_name} and {name} in editor-written text.

    A body that happens to contain braces (a price range, say) must not
    break the send, so a format error leaves the text as written.
    """
    first = (getattr(reader, "name", "") or "").split(" ")[0]
    try:
        return (text or "").format(first_name=first, name=f"{first}, " if first else "")
    except (KeyError, IndexError, ValueError):
        return text or ""


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        CANCELLED = "cancelled", "Cancelled"

    # --- The email ------------------------------------------------------
    name = models.CharField(
        max_length=120,
        help_text="For you, not the reader - e.g. 'Frieze weekend' or 'Venue change'.")
    subject = models.CharField(
        max_length=200,
        help_text="Placeholders: {first_name}, and {name} (their first name plus a "
                  "comma, or nothing if we don't have one).")
    brief = models.TextField(
        blank=True,
        help_text="What this email is about, in your words: the facts, why it matters, "
                  "what you'd like readers to do. This is the only source of facts the "
                  "AI may use - if it isn't here, it won't be in the email.")
    body = models.TextField(
        blank=True,
        help_text="The email as you'd write it yourself, paragraphs separated by blank "
                  "lines. With personalisation on it's source material and each reader "
                  "gets their own version; off, it goes out exactly as written. "
                  "{first_name} works here too.")
    personalise = models.BooleanField(
        default=True, verbose_name="Personalise with AI",
        help_text="Write each reader's version from the brief and their profile, in the "
                  "newsletter's voice. If AI is off or unavailable, the body goes out as "
                  "written instead.")
    link_label = models.CharField(
        max_length=60, blank=True, default="Book / learn more",
        help_text="Text on the button under the email, if there is a link.")
    link_url = models.URLField(blank=True, help_text="Optional button under the text.")

    # The event this is about, if it is about one. The angle is the part
    # worth keeping when the same campaign is run again for another event:
    # "for people who like small rooms" outlives the Trio residency.
    angle = models.CharField(
        max_length=200, blank=True,
        help_text="The take, in a few words - who this is for and why. Kept when you "
                  "run the campaign again for another event.")
    event = models.ForeignKey(
        "opportunities.Opportunity", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="campaigns",
        help_text="The event this campaign is about, if any. Its facts are the brief.")

    # --- Audience -------------------------------------------------------
    audience_categories = models.JSONField(
        default=list, blank=True,
        help_text="Only readers who follow at least one of these. Empty means no "
                  "restriction.")
    audience_tags = models.ManyToManyField(
        Tag, blank=True, related_name="campaigns",
        help_text="Only readers who picked at least one of these interests. Empty "
                  "means no restriction.")
    audience_location = models.CharField(
        max_length=120, blank=True,
        help_text="Only readers whose location contains this, e.g. 'London'. Empty "
                  "means everywhere.")

    # --- Schedule and state ---------------------------------------------
    class Frequency(models.TextChoices):
        ONCE = "once", "Once"
        DAILY = "daily", "Every day"
        WEEKLY = "weekly", "Every week"
        FORTNIGHTLY = "fortnightly", "Every two weeks"
        MONTHLY = "monthly", "Every month"

    send_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When to send. Leave blank to send as soon as it's scheduled. The "
                  "scheduler checks every few minutes, so a time is honoured to within "
                  "that.")
    frequency = models.CharField(
        max_length=20, choices=Frequency.choices, default=Frequency.ONCE,
        help_text="Once, or on repeat between the start and end dates below. A "
                  "repeating campaign emails its whole audience again on every run.")
    starts_on = models.DateField(
        null=True, blank=True,
        help_text="First day a repeating campaign may run. Blank means it may run "
                  "from now.")
    ends_on = models.DateField(
        null=True, blank=True,
        help_text="Last day it may run. Blank means it repeats until you cancel it.")
    send_hour = models.PositiveSmallIntegerField(
        default=9,
        help_text="Hour of the day a repeating campaign goes out, 0-23, in the "
                  "site's time zone.")
    last_run_on = models.DateField(
        null=True, blank=True, editable=False,
        help_text="Set after each run so one day never sends twice.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(
        blank=True, help_text="The most recent failure, if any. Per-reader detail is "
                              "in the deliveries below.")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        editable=False, related_name="campaigns")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    # --- validation -----------------------------------------------------

    def clean(self):
        if not (self.body or "").strip() and not (self.brief or "").strip():
            raise ValidationError(
                "Write a brief or a body - there's nothing to send otherwise.")
        probe = Reader(name="Ada Example")
        if "{" in self.subject and fill_placeholders(self.subject, probe) == self.subject:
            raise ValidationError({
                "subject": "Only {first_name} and {name} are understood here."})
        if self.link_url and not self.link_label:
            self.link_label = "Book / learn more"

    # --- who gets it ----------------------------------------------------

    def audience(self):
        """Active readers matching every restriction that is set."""
        readers = Reader.objects.filter(is_active=True)
        if self.audience_location:
            readers = readers.filter(location__icontains=self.audience_location.strip())
        if self.pk and self.audience_tags.exists():
            readers = readers.filter(interest_tags__in=self.audience_tags.all()).distinct()
        wanted = set(self.audience_categories or [])
        if wanted:
            # JSON containment lookups differ between SQLite and Postgres;
            # the reader list is small enough to check in Python.
            ids = [
                r.pk for r in readers.only("pk", "interest_categories")
                if wanted & set(r.interest_categories or [])
            ]
            readers = Reader.objects.filter(pk__in=ids)
        return readers.order_by("pk")

    def audience_description(self) -> str:
        parts = []
        if self.audience_categories:
            parts.append("follow " + " or ".join(self.audience_categories))
        if self.pk:
            tags = list(self.audience_tags.values_list("name", flat=True))
            if tags:
                parts.append("picked " + " or ".join(tags))
        if self.audience_location:
            parts.append(f"are in {self.audience_location.strip()}")
        return "Every active reader" if not parts else "Active readers who " + " and ".join(parts)

    # --- state ----------------------------------------------------------

    def is_due(self, now=None) -> bool:
        now = now or timezone.now()
        return self.status == self.Status.SCHEDULED and (
            self.send_at is None or self.send_at <= now)

    @property
    def is_sendable(self) -> bool:
        return self.status in (self.Status.SCHEDULED, self.Status.SENDING)

    def progress(self) -> dict:
        counts = dict(
            self.deliveries.values_list("status").annotate(n=Count("id"))
        ) if self.pk else {}
        report = {value: counts.get(value, 0) for value in CampaignDelivery.Status.values}
        report["total"] = sum(counts.values())
        return report


    # --- repeating ------------------------------------------------------

    def repeats(self) -> bool:
        return self.frequency != self.Frequency.ONCE

    def due_for_a_run(self, now=None) -> tuple[bool, str]:
        """Should a repeating campaign go out again? (yes/no, why).

        The day rules live here rather than in cron syntax, for the same
        reason the newsletter's do: an editor can see and change them.
        """
        from datetime import timedelta

        from django.utils import timezone

        if not self.repeats():
            return False, "Not a repeating campaign."
        if self.status in (self.Status.CANCELLED, self.Status.DRAFT):
            return False, f"{self.get_status_display()}."

        now = timezone.localtime(now or timezone.now())
        today = now.date()
        if self.starts_on and today < self.starts_on:
            return False, f"Starts on {self.starts_on}."
        if self.ends_on and today > self.ends_on:
            return False, f"Finished on {self.ends_on}."
        if self.last_run_on == today:
            return False, "Already run today."
        if now.hour < self.send_hour:
            return False, f"Due today, but not until {self.send_hour:02d}:00."

        anchor = self.last_run_on or self.starts_on
        if self.frequency == self.Frequency.DAILY:
            return True, "Daily."
        if self.frequency == self.Frequency.WEEKLY:
            if anchor and (today - anchor) < timedelta(days=7):
                return False, f"Last run {anchor}; not a week yet."
            return True, "Weekly."
        if self.frequency == self.Frequency.FORTNIGHTLY:
            if anchor and (today - anchor) < timedelta(days=13):
                return False, f"Last run {anchor}; not two weeks yet."
            return True, "Fortnightly."
        if self.frequency == self.Frequency.MONTHLY:
            if anchor and (today - anchor) < timedelta(days=27):
                return False, f"Last run {anchor}; not a month yet."
            return True, "Monthly."
        return False, "Unrecognised frequency."

    def begin_repeat_run(self):
        """Put a finished repeating campaign back in the queue.

        Every reader in the audience gets it again - that is what a
        repeating campaign is - so the existing delivery rows are reset
        rather than duplicated. The Results tab therefore shows the
        current run, which is the one anybody asks about.
        """
        from django.utils import timezone

        self.deliveries.update(status=CampaignDelivery.Status.PENDING,
                               error="", sent_at=None, provider_message_id="")
        self.status = self.Status.SCHEDULED
        self.last_run_on = timezone.localdate()
        self.started_at = None
        self.finished_at = None
        self.save(update_fields=["status", "last_run_on", "started_at",
                                 "finished_at", "updated_at"])


class CampaignDelivery(models.Model):
    """One reader's copy of one campaign: what they were sent, and whether."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="deliveries")
    reader = models.ForeignKey(Reader, on_delete=models.CASCADE,
                               related_name="campaign_deliveries")
    # Identifies this reader's copy in a link, so a click on the button can
    # be attributed without putting an email address in a URL.
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    personalised = models.BooleanField(
        default=False, help_text="Whether AI wrote this reader's version.")
    subject = models.CharField(max_length=200, blank=True)
    body_text = models.TextField(blank=True, help_text="Exactly what this reader was sent.")
    provider_message_id = models.CharField(max_length=120, blank=True)
    error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("campaign", "reader")]
        ordering = ["campaign", "pk"]
        verbose_name_plural = "campaign deliveries"

    def __str__(self):
        return f"{self.campaign} -> {self.reader} ({self.get_status_display()})"
