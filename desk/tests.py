"""The desk: a from-scratch admin that owns every template and stylesheet
it uses, so nothing here can be undone by Django's contrib.admin assets.

Coverage mirrors what the desk replaces: every list and form page renders,
staff-only access is enforced, and each action (publish/archive, send/
preview a newsletter, campaign scheduling, template start-from-builtin)
does what it says.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

# The production static storage needs a manifest built by collectstatic,
# which tests don't run - swap it out for anything that renders desk pages.
plain_static = override_settings(STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})

from campaigns.models import Campaign, CampaignDelivery
from opportunities.models import Opportunity, Tag
from readers.models import Reader
from recommendations.models import NewsletterIssue, Recommendation
from siteconfig.emails import EmailTemplate
from siteconfig.models import SiteConfig


def staff_user(**kwargs):
    User = get_user_model()
    defaults = {"is_staff": True, "is_active": True}
    defaults.update(kwargs)
    user = User.objects.create_user("editor", "editor@example.com", "pw", **defaults)
    return user


def opportunity(**kwargs):
    defaults = {
        "title": "Trio residency", "category": "music", "description": "A jazz trio.",
        "price_tier": "budget", "location_area": "London", "booking_url": "https://example.com",
        "mainstream_to_unusual": 3, "intimate_to_large_scale": 2,
    }
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


@plain_static
class AccessTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("desk:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("desk:login"), response.url)

    def test_a_non_staff_user_is_refused_not_looped(self):
        User = get_user_model()
        User.objects.create_user("reader", "r@example.com", "pw", is_staff=False)
        self.client.login(username="reader", password="pw")
        response = self.client.get(reverse("desk:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_a_staff_user_gets_in(self):
        staff_user()
        self.client.login(username="editor", password="pw")
        response = self.client.get(reverse("desk:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_the_login_page_renders_signed_out(self):
        response = self.client.get(reverse("desk:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editorial desk")


@plain_static
class LoggedInTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = staff_user()
        self.client.login(username="editor", password="pw")


class PageRenderTests(LoggedInTestCase):
    """Every list and add page has to render with no data at all - the
    empty state is a real code path, not just a full one."""

    def test_every_list_and_add_page_renders_empty(self):
        urls = [
            reverse("desk:dashboard"), reverse("desk:listings_list"), reverse("desk:listings_add"),
            reverse("desk:interests_list"), reverse("desk:interests_add"),
            reverse("desk:readers_list"), reverse("desk:campaigns_list"), reverse("desk:campaigns_add"),
            reverse("desk:issues_list"), reverse("desk:recommendations_list"),
            reverse("desk:templates_list"), reverse("desk:templates_add"), reverse("desk:siteconfig"),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_change_pages_render_with_data(self):
        opp = opportunity()
        tag = Tag.objects.create(name="Jazz nights", category="music")
        reader = Reader.objects.create(email="ada@example.com", name="Ada")
        campaign = Campaign.objects.create(name="c", subject="s", body="b")
        template = EmailTemplate.objects.create(
            name="t", kind=EmailTemplate.Kind.NEWSLETTER, html_body="<p>{{ reader.name }}</p>")
        issue = NewsletterIssue.objects.create(reader=reader)
        Recommendation.objects.create(issue=issue, opportunity=opp, rationale="x")

        for url in [
            reverse("desk:listings_change", args=[opp.pk]),
            reverse("desk:interests_change", args=[tag.pk]),
            reverse("desk:readers_change", args=[reader.pk]),
            reverse("desk:campaigns_change", args=[campaign.pk]),
            reverse("desk:campaigns_preview", args=[campaign.pk]),
            reverse("desk:templates_change", args=[template.pk]),
            reverse("desk:templates_preview", args=[template.pk]),
            reverse("desk:issues_change", args=[issue.pk]),
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)


class ListingWorkflowTests(LoggedInTestCase):
    def test_add_a_listing(self):
        # A slug no seeded tag uses.
        tag = Tag.objects.create(name="Campaign test interest", slug="campaign-test-interest", category="music")
        response = self.client.post(reverse("desk:listings_add"), {
            "title": "A show", "category": "music", "status": "draft", "tags": [tag.pk],
            "description": "desc", "editorial_note": "", "price_tier": "budget",
            "price_display": "", "location_name": "", "location_area": "London",
            "is_online": "", "booking_url": "https://example.com",
            "start_date": "", "end_date": "", "critic_rating": "", "critic_rating_source": "",
            "critic_quote": "", "mainstream_to_unusual": 3, "intimate_to_large_scale": 2,
        })
        self.assertEqual(response.status_code, 302)
        obj = Opportunity.objects.get(title="A show")
        self.assertEqual(obj.created_by, self.user)
        self.assertIn(tag, obj.tags.all())

    def test_bulk_publish(self):
        opp = opportunity(status=Opportunity.Status.DRAFT)
        self.client.post(reverse("desk:listings_list"), {"action": "publish", "selected": [opp.pk]})
        opp.refresh_from_db()
        self.assertEqual(opp.status, Opportunity.Status.PUBLISHED)

    def test_search_and_filter(self):
        opportunity(title="Jazz basement", category="music", description="A basement bar.")
        opportunity(title="Pottery class", category="talk", description="Hand-building for beginners.")
        response = self.client.get(reverse("desk:listings_list"), {"q": "Jazz"})
        self.assertContains(response, "Jazz basement")
        self.assertNotContains(response, "Pottery class")
        response = self.client.get(reverse("desk:listings_list"), {"category": "talk"})
        self.assertContains(response, "Pottery class")
        self.assertNotContains(response, "Jazz basement")

    def test_load_sample_catalogue(self):
        before = Opportunity.objects.count()
        response = self.client.post(reverse("desk:listings_add"), {"load_sample_catalogue": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertGreater(Opportunity.objects.count(), before)


class ReaderWorkflowTests(LoggedInTestCase):
    @patch("desk.views.readers.send_issue_for_reader")
    def test_preview_and_send_actions(self, send):
        from recommendations.sending import SendResult

        send.return_value = SendResult(reader_email="x", sent=True, match_count=2, message="Sent 2.")
        reader = Reader.objects.create(email="ada@example.com")
        response = self.client.post(reverse("desk:readers_change", args=[reader.pk]),
                                    {"action": "send"})
        self.assertEqual(response.status_code, 302)
        send.assert_called_once_with(reader, dry_run=False)

    def test_filters_by_follows_and_status(self):
        Reader.objects.create(email="a@example.com", interest_categories=["music"])
        Reader.objects.create(email="b@example.com", interest_categories=[])
        response = self.client.get(reverse("desk:readers_list"), {"follows": "music"})
        self.assertContains(response, "a@example.com")
        self.assertNotContains(response, "b@example.com")


@override_settings(SENDGRID_API_KEY="test-key")
class CampaignWorkflowTests(LoggedInTestCase):
    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_schedule_then_send_now(self, deliver):
        Reader.objects.create(email="ada@example.com")
        campaign = Campaign.objects.create(name="c", subject="s", body="Hello.")
        self.client.post(reverse("desk:campaigns_schedule", args=[campaign.pk]))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.SCHEDULED)

        self.client.post(reverse("desk:campaigns_send_now", args=[campaign.pk]))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.SENT)
        deliver.assert_called_once()

    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_a_test_send_goes_to_the_editor(self, deliver):
        Reader.objects.create(email="ada@example.com", name="Ada")
        campaign = Campaign.objects.create(name="c", subject="s", body="Hello {first_name}.")
        self.client.post(reverse("desk:campaigns_test_send", args=[campaign.pk]))
        self.assertEqual(deliver.call_args.args[0], "editor@example.com")
        self.assertFalse(campaign.deliveries.exists())

    def test_state_changes_need_a_post(self):
        campaign = Campaign.objects.create(name="c", subject="s", body="b")
        response = self.client.get(reverse("desk:campaigns_send_now", args=[campaign.pk]))
        self.assertEqual(response.status_code, 405)

    def test_retry_failed_deliveries(self):
        campaign = Campaign.objects.create(name="c", subject="s", body="b", status=Campaign.Status.SENT)
        reader = Reader.objects.create(email="a@example.com")
        CampaignDelivery.objects.create(campaign=campaign, reader=reader,
                                        status=CampaignDelivery.Status.FAILED, error="boom")
        self.client.post(reverse("desk:campaigns_list"), {"action": "retry_failed", "selected": [campaign.pk]})
        self.assertEqual(
            campaign.deliveries.get().status, CampaignDelivery.Status.PENDING)


class EmailTemplateWorkflowTests(LoggedInTestCase):
    def test_start_from_builtin_needs_a_post_and_creates_a_copy(self):
        url = reverse("desk:templates_from_builtin", args=["newsletter"])
        self.assertEqual(self.client.get(url).status_code, 405)
        before = EmailTemplate.objects.count()
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EmailTemplate.objects.count(), before + 1)

    def test_a_broken_template_is_rejected_on_save(self):
        response = self.client.post(reverse("desk:templates_add"), {
            "name": "bad", "kind": "newsletter", "subject": "", "notes": "",
            "html_body": "{% broken %}", "text_body": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "won&#x27;t render")
        self.assertFalse(EmailTemplate.objects.filter(name="bad").exists())


class SiteConfigTests(LoggedInTestCase):
    def test_saving_updates_the_singleton(self):
        config = SiteConfig.load()
        data = {f.name: getattr(config, f.name) for f in SiteConfig._meta.fields
                if f.name not in ("id", "updated_at") and not f.name.endswith("_id")}
        data = {k: ("" if v is None else v) for k, v in data.items()}
        data["site_name"] = "New Name"
        data["send_frequency"] = SiteConfig.Frequency.MANUAL
        data["send_weekday"] = 0
        data["send_day_of_month"] = 1
        data["send_hour"] = 8
        # Foreign keys serialize as their pk field name in a form post.
        for fk in ("welcome_template", "newsletter_template", "campaign_template"):
            data.pop(fk, None)
            data[fk] = ""
        response = self.client.post(reverse("desk:siteconfig"), data)
        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.status_code == 200 else None)
        self.assertEqual(SiteConfig.load().site_name, "New Name")

    def test_reset_wording(self):
        config = SiteConfig.load()
        config.site_name = "Drifted"
        config.save()
        self.client.post(reverse("desk:siteconfig_reset_wording"))
        self.assertEqual(SiteConfig.load().site_name, "The Ether")
