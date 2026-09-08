import threading
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from opportunities.models import Tag
from readers.models import Reader
from recommendations.models import NewsletterIssue
from recommendations.sending import SendResult, send_scheduled_newsletter
from siteconfig.models import SiteConfig

from . import scheduler
from .models import Campaign, CampaignDelivery
from .sending import send_campaign

# The production static storage needs a manifest built by collectstatic,
# which tests don't run - swap it out for anything that renders admin pages.
plain_static = override_settings(STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})


def campaign(**kwargs):
    defaults = {
        "name": "Frieze weekend", "subject": "{name}this weekend at Frieze",
        "body": "Hello {first_name}. Doors at 7, tickets are £12.",
        "personalise": False, "status": Campaign.Status.SCHEDULED,
    }
    return Campaign.objects.create(**{**defaults, **kwargs})


class AudienceTests(TestCase):
    def setUp(self):
        cache.clear()
        # A slug no seeded tag uses - the vocabulary migration already has "jazz".
        self.jazz = Tag.objects.create(name="Campaign test tag", slug="campaign-test-tag",
                                       category="music")
        self.ada = Reader.objects.create(
            email="ada@example.com", name="Ada Lovelace", location="London",
            interest_categories=["music"])
        self.ada.interest_tags.add(self.jazz)
        self.bob = Reader.objects.create(
            email="bob@example.com", name="Bob", location="Brighton",
            interest_categories=["theatre"])
        Reader.objects.create(email="gone@example.com", is_active=False)

    def emails(self, c):
        return set(c.audience().values_list("email", flat=True))

    def test_everyone_active_when_nothing_is_set(self):
        self.assertEqual(self.emails(campaign()), {"ada@example.com", "bob@example.com"})

    def test_categories_narrow_it(self):
        self.assertEqual(self.emails(campaign(audience_categories=["music"])),
                         {"ada@example.com"})

    def test_tags_narrow_it(self):
        c = campaign()
        c.audience_tags.add(self.jazz)
        self.assertEqual(self.emails(c), {"ada@example.com"})

    def test_location_narrows_it(self):
        self.assertEqual(self.emails(campaign(audience_location="brigh")),
                         {"bob@example.com"})

    def test_a_campaign_needs_something_to_send(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            Campaign(name="x", subject="s").clean()


@override_settings(SENDGRID_API_KEY="test-key")
class SendCampaignTests(TestCase):
    def setUp(self):
        cache.clear()
        self.ada = Reader.objects.create(email="ada@example.com", name="Ada Lovelace")
        self.bob = Reader.objects.create(email="bob@example.com", name="Bob")

    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_everyone_gets_their_own_copy_and_it_is_recorded(self, deliver):
        c = campaign()
        run = send_campaign(c, budget_seconds=30)

        self.assertEqual((run.sent, run.failed, run.remaining), (2, 0, 0))
        self.assertTrue(run.finished)
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.SENT)
        self.assertIsNotNone(c.finished_at)

        ada = c.deliveries.get(reader=self.ada)
        self.assertEqual(ada.status, CampaignDelivery.Status.SENT)
        self.assertEqual(ada.subject, "Ada, this weekend at Frieze")
        self.assertTrue(ada.body_text.startswith("Hello Ada."))
        self.assertEqual(ada.provider_message_id, "msg-1")
        self.assertFalse(ada.personalised)

        to_addresses = {call.args[0] for call in deliver.call_args_list}
        self.assertEqual(to_addresses, {"ada@example.com", "bob@example.com"})

    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_running_again_does_not_resend(self, deliver):
        c = campaign()
        send_campaign(c, budget_seconds=30)
        c.refresh_from_db()
        c.status = Campaign.Status.SENDING  # as if a run were interrupted
        c.save()
        send_campaign(c, budget_seconds=30)
        self.assertEqual(deliver.call_count, 2)

    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_out_of_time_leaves_the_rest_for_the_scheduler(self, deliver):
        c = campaign()
        run = send_campaign(c, budget_seconds=0)
        self.assertEqual(run.attempted, 0)
        self.assertEqual(run.remaining, 2)
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.SENDING)
        self.assertIn("scheduler carries on", run.message)

    @override_settings(SENDGRID_API_KEY="")
    def test_without_a_provider_key_nothing_is_claimed_as_sent(self):
        c = campaign()
        run = send_campaign(c, budget_seconds=30)
        self.assertEqual((run.sent, run.skipped), (0, 2))
        self.assertEqual(
            set(c.deliveries.values_list("status", flat=True)),
            {CampaignDelivery.Status.SKIPPED})

    @patch("campaigns.sending.deliver", side_effect=RuntimeError("rejected: unverified sender"))
    def test_a_provider_rejection_is_recorded_per_reader(self, deliver):
        c = campaign()
        run = send_campaign(c, budget_seconds=30)
        self.assertEqual(run.failed, 2)
        c.refresh_from_db()
        self.assertIn("unverified sender", c.last_error)
        self.assertIn("unverified sender", c.deliveries.first().error)

    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_a_draft_is_never_sent(self, deliver):
        c = campaign(status=Campaign.Status.DRAFT)
        run = send_campaign(c, budget_seconds=30)
        self.assertEqual(run.attempted, 0)
        deliver.assert_not_called()
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.DRAFT)

    @patch("campaigns.sending.deliver", return_value="msg-1")
    @patch("recommendations.ai._call", return_value={"body": "Written for you, from the brief."})
    def test_ai_writes_each_readers_version(self, _call, deliver):
        c = campaign(personalise=True, brief="Frieze is on this weekend.", body="")
        send_campaign(c, budget_seconds=30)
        ada = c.deliveries.get(reader=self.ada)
        self.assertEqual(ada.body_text, "Written for you, from the brief.")
        self.assertTrue(ada.personalised)

    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_without_ai_the_body_goes_as_written(self, deliver):
        c = campaign(personalise=True)  # no OPENAI_API_KEY in tests
        send_campaign(c, budget_seconds=30)
        ada = c.deliveries.get(reader=self.ada)
        self.assertTrue(ada.body_text.startswith("Hello Ada."))
        self.assertFalse(ada.personalised)

    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_the_email_carries_an_unsubscribe_link(self, deliver):
        c = campaign(link_url="https://example.com/frieze")
        send_campaign(c, budget_seconds=30)
        _, subject, text, html = deliver.call_args_list[0].args
        self.assertIn(str(self.ada.unsubscribe_token), html)
        self.assertIn(str(self.ada.unsubscribe_token), text)
        self.assertIn("https://example.com/frieze", html)


class ScheduledRunTests(TestCase):
    def setUp(self):
        cache.clear()
        Reader.objects.create(email="ada@example.com", name="Ada")

    @override_settings(SENDGRID_API_KEY="test-key")
    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_only_what_is_due_goes_out(self, deliver):
        now = timezone.now()
        due = campaign(name="due", send_at=now - timedelta(minutes=1))
        asap = campaign(name="asap", send_at=None)
        later = campaign(name="later", send_at=now + timedelta(hours=2))
        cancelled = campaign(name="cancelled", status=Campaign.Status.CANCELLED)
        draft = campaign(name="draft", status=Campaign.Status.DRAFT)

        out = StringIO()
        call_command("run_scheduled", stdout=out)

        statuses = {c.name: Campaign.objects.get(pk=c.pk).status
                    for c in (due, asap, later, cancelled, draft)}
        self.assertEqual(statuses, {
            "due": Campaign.Status.SENT, "asap": Campaign.Status.SENT,
            "later": Campaign.Status.SCHEDULED, "cancelled": Campaign.Status.CANCELLED,
            "draft": Campaign.Status.DRAFT,
        })
        self.assertEqual(deliver.call_count, 2)
        self.assertIn("Newsletter: not now", out.getvalue())

    def test_dry_run_sends_nothing(self):
        c = campaign(name="asap")
        out = StringIO()
        call_command("run_scheduled", dry_run=True, stdout=out)
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.SCHEDULED)
        self.assertIn("Would send 'asap' to 1 reader", out.getvalue())

    def test_the_newsletter_goes_on_its_day_once_the_hour_has_passed(self):
        config = SiteConfig.load()
        config.send_frequency = SiteConfig.Frequency.WEEKLY
        config.send_weekday = timezone.localtime().weekday()
        config.send_hour = 0
        config.save()
        out = StringIO()
        call_command("run_scheduled", stdout=out)
        # The one reader has nothing to be matched against, so they are
        # skipped - but the pass reached the end, so the day is done.
        self.assertIn("today's send is complete", out.getvalue())
        self.assertEqual(SiteConfig.load().last_sent_on, timezone.localdate())


class SendHourTests(TestCase):
    def setUp(self):
        cache.clear()
        self.config = SiteConfig.load()
        self.config.send_frequency = SiteConfig.Frequency.WEEKLY
        self.config.send_weekday = SiteConfig.Weekday.THURSDAY
        self.config.send_hour = 8

    def test_send_day_but_too_early(self):
        thursday_7am = datetime(2026, 9, 10, 7, 0, tzinfo=dt_timezone.utc)
        ok, why = self.config.should_send_now(thursday_7am)
        self.assertFalse(ok)
        self.assertIn("08:00", why)

    def test_send_day_once_the_hour_arrives(self):
        thursday_9am = datetime(2026, 9, 10, 9, 0, tzinfo=dt_timezone.utc)
        ok, _ = self.config.should_send_now(thursday_9am)
        self.assertTrue(ok)

    def test_not_the_send_day(self):
        friday = datetime(2026, 9, 11, 9, 0, tzinfo=dt_timezone.utc)
        ok, _ = self.config.should_send_now(friday)
        self.assertFalse(ok)


@plain_static
def _weekly_today():
    config = SiteConfig.load()
    config.send_frequency = SiteConfig.Frequency.WEEKLY
    config.send_weekday = timezone.localtime().weekday()
    config.send_hour = 0
    config.save()
    return config


class ScheduledNewsletterPassTests(TestCase):
    """The cadence send in short, resumable passes."""

    def setUp(self):
        cache.clear()
        self.ada = Reader.objects.create(email="ada@example.com", name="Ada")
        self.bob = Reader.objects.create(email="bob@example.com", name="Bob")
        _weekly_today()

    def test_not_a_send_moment_does_nothing(self):
        config = SiteConfig.load()
        config.send_frequency = SiteConfig.Frequency.MANUAL
        config.save()
        report = send_scheduled_newsletter(budget_seconds=30)
        self.assertFalse(report["ran"])
        self.assertIn("manual", report["why"].lower())

    @patch("recommendations.sending.send_issue_for_reader")
    def test_readers_already_sent_today_are_not_sent_again(self, send):
        send.return_value = SendResult(reader_email="x", sent=True, match_count=3, message="ok")
        NewsletterIssue.objects.create(reader=self.ada)
        report = send_scheduled_newsletter(budget_seconds=30)
        self.assertEqual([c.args[0] for c in send.call_args_list], [self.bob])
        self.assertTrue(report["done"])
        self.assertEqual(SiteConfig.load().last_sent_on, timezone.localdate())

    @patch("recommendations.sending.send_issue_for_reader")
    def test_out_of_budget_leaves_readers_for_the_next_pass(self, send):
        report = send_scheduled_newsletter(budget_seconds=0)
        send.assert_not_called()
        self.assertEqual(report["remaining"], 2)
        self.assertFalse(report["done"])
        self.assertIsNone(SiteConfig.load().last_sent_on)

    def test_a_completed_pass_stops_further_passes_today(self):
        report = send_scheduled_newsletter(budget_seconds=30)  # nobody matches: all skipped
        self.assertEqual((report["skipped"], report["done"]), (2, True))
        again = send_scheduled_newsletter(budget_seconds=30)
        self.assertFalse(again["ran"])
        self.assertIn("Already sent today", again["why"])


class SchedulerPingTests(TestCase):
    """The URL an external monitor hits in place of cron."""

    def url(self):
        return reverse("run-scheduled")

    @override_settings(SCHEDULER_TOKEN="")
    def test_refuses_when_no_token_is_configured(self):
        self.assertEqual(self.client.get(self.url()).status_code, 503)

    @override_settings(SCHEDULER_TOKEN="s3cret")
    def test_rejects_a_missing_or_wrong_token(self):
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.get(self.url(), {"token": "nope"}).status_code, 403)

    @override_settings(SCHEDULER_TOKEN="s3cret", SCHEDULER_BUDGET_SECONDS=42)
    @patch("campaigns.views.start_background_run", return_value=True)
    def test_a_valid_ping_starts_a_pass_and_returns_at_once(self, start):
        response = self.client.get(self.url(), HTTP_X_SCHEDULER_TOKEN="s3cret")
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["started"])
        start.assert_called_once_with(42)
        # Query-string form, for monitors that cannot set headers.
        self.assertEqual(self.client.get(self.url(), {"token": "s3cret"}).status_code, 202)

    @override_settings(SCHEDULER_TOKEN="s3cret")
    @patch("campaigns.views.start_background_run", return_value=False)
    def test_says_so_when_a_pass_is_already_running(self, start):
        response = self.client.get(self.url(), {"token": "s3cret"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["started"])

    @override_settings(SCHEDULER_TOKEN="s3cret")
    @patch("campaigns.views.start_background_run")
    def test_head_checks_liveness_without_starting_anything(self, start):
        self.assertEqual(self.client.head(self.url(), {"token": "s3cret"}).status_code, 200)
        start.assert_not_called()


class BackgroundRunTests(TestCase):
    def test_only_one_pass_runs_at_a_time(self):
        cache.clear()
        gate = threading.Event()

        def slow(budget_seconds, dry_run=False):
            gate.wait(5)
            return {"finished_at": timezone.now(), "seconds": 0.0, "lines": ["ok"]}

        with patch("campaigns.scheduler.run_due", side_effect=slow):
            self.assertTrue(scheduler.start_background_run(1))
            self.assertTrue(scheduler.is_running())
            self.assertFalse(scheduler.start_background_run(1))
            gate.set()
            for _ in range(200):
                if not scheduler.is_running():
                    break
                time.sleep(0.02)
        self.assertFalse(scheduler.is_running())
        self.assertEqual(scheduler.last_run()["lines"], ["ok"])
