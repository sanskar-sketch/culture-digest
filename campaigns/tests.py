from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from opportunities.models import Tag
from readers.models import Reader
from siteconfig.models import SiteConfig

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

    @patch("campaigns.management.commands.run_scheduled.call_command")
    def test_the_newsletter_goes_on_its_day_once_the_hour_has_passed(self, call):
        config = SiteConfig.load()
        config.send_frequency = SiteConfig.Frequency.WEEKLY
        config.send_weekday = timezone.localtime().weekday()
        config.send_hour = 0
        config.save()
        cache.clear()
        call_command("run_scheduled", stdout=StringIO())
        call.assert_called_once()
        self.assertEqual(call.call_args.args[0], "send_newsletters")
        self.assertTrue(call.call_args.kwargs["scheduled"])


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
class CampaignAdminTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.editor = User.objects.create_superuser(
            "editor", "editor@example.com", "pw")
        self.client.force_login(self.editor)
        self.ada = Reader.objects.create(email="ada@example.com", name="Ada Lovelace")

    def test_the_pages_render(self):
        c = campaign(status=Campaign.Status.DRAFT)
        for url in (
            reverse("admin:campaigns_campaign_changelist"),
            reverse("admin:campaigns_campaign_add"),
            reverse("admin:campaigns_campaign_change", args=[c.pk]),
            reverse("admin:campaigns_campaign_preview", args=[c.pk]),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_preview_is_written_for_a_real_reader(self):
        c = campaign(status=Campaign.Status.DRAFT)
        response = self.client.get(reverse("admin:campaigns_campaign_preview", args=[c.pk]))
        self.assertContains(response, "Ada Lovelace")
        self.assertContains(response, "Ada, this weekend at Frieze")

    def test_schedule_then_send_now_from_the_buttons(self):
        c = campaign(status=Campaign.Status.DRAFT)
        self.client.post(reverse("admin:campaigns_campaign_schedule", args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.SCHEDULED)

        with override_settings(SENDGRID_API_KEY="test-key"), \
                patch("campaigns.sending.deliver", return_value="msg-1") as deliver:
            self.client.post(reverse("admin:campaigns_campaign_send_now", args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.SENT)
        self.assertEqual(deliver.call_args.args[0], "ada@example.com")

    def test_state_changes_need_a_post(self):
        c = campaign(status=Campaign.Status.DRAFT)
        response = self.client.get(reverse("admin:campaigns_campaign_send_now", args=[c.pk]))
        self.assertEqual(response.status_code, 405)
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.DRAFT)

    @override_settings(SENDGRID_API_KEY="test-key")
    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_a_test_send_goes_to_the_editor_not_the_reader(self, deliver):
        c = campaign(status=Campaign.Status.DRAFT)
        self.client.post(reverse("admin:campaigns_campaign_test_send", args=[c.pk]))
        self.assertEqual(deliver.call_count, 1)
        self.assertEqual(deliver.call_args.args[0], "editor@example.com")
        self.assertTrue(deliver.call_args.args[1].startswith("[Test]"))
        self.assertFalse(c.deliveries.exists())

    def test_cancelling_stops_anything_not_yet_sent(self):
        c = campaign()
        self.client.post(reverse("admin:campaigns_campaign_cancel", args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.status, Campaign.Status.CANCELLED)
        self.assertEqual(send_campaign(c, budget_seconds=30).attempted, 0)

    def test_campaigns_appear_in_the_sidebar_and_index(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, reverse("admin:campaigns_campaign_changelist"))


@plain_static
class PreviewEscapingTests(TestCase):
    """The rendered email is a SafeString. Dropped into an attribute unescaped,
    its first double quote ends the srcdoc early and the iframe shows nothing."""

    def setUp(self):
        cache.clear()
        self.client.force_login(get_user_model().objects.create_superuser("e", "e@x.com", "pw"))
        Reader.objects.create(email="ada@example.com", name="Ada")

    def test_the_email_survives_the_srcdoc_attribute(self):
        c = campaign(status=Campaign.Status.DRAFT)
        response = self.client.get(reverse("admin:campaigns_campaign_preview", args=[c.pk]))
        self.assertContains(response, 'srcdoc="&lt;!doctype html&gt;')
        self.assertNotContains(response, 'srcdoc="<!doctype')
