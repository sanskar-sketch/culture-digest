"""Campaigns from an idea, and the five-entry sidebar."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from campaigns.models import Campaign, CampaignDelivery
from opportunities.models import Opportunity, Tag
from readers.models import Reader
from recommendations.models import NewsletterIssue

from .tests import plain_static


def listing(title="Gig", **kw):
    data = dict(title=title, category="music", description="x", price_tier="budget",
                location_area="London", booking_url="https://example.com/g",
                mainstream_to_unusual=3, intimate_to_large_scale=2,
                status=Opportunity.Status.PUBLISHED)
    data.update(kw)
    return Opportunity.objects.create(**data)


@plain_static
class DeskCase(TestCase):
    def setUp(self):
        cache.clear()
        self.boss = get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.login(username="boss", password="pw")


class SidebarTests(DeskCase):
    """Five entries, in the order the user asked for, and nothing else."""

    def test_the_sidebar_has_exactly_the_five_sections(self):
        html = self.client.get(reverse("desk:dashboard")).content.decode()
        nav = html.split('<div class="d-nav">', 1)[1].split('<div class="d-side-foot">', 1)[0]
        for name in ("Events", "Interests", "Users", "Campaigns", "Insights"):
            self.assertIn(f">{name}</a>", nav)
        for gone in ("Listings", "Templates", "Send by interest", "Newsletter issues",
                     "Recommendations", "Email templates", "Site configuration", "Groups"):
            self.assertNotIn(f">{gone}</a>", nav)

    def test_settings_and_accounts_sit_in_the_footer(self):
        html = self.client.get(reverse("desk:dashboard")).content.decode()
        foot = html.split('<div class="d-side-foot">', 1)[1].split("</nav>", 1)[0]
        self.assertIn(">Settings</a>", foot)
        self.assertIn(">Editor accounts</a>", foot)

    def test_an_editor_does_not_see_editor_accounts(self):
        get_user_model().objects.create_user("ed", "e@example.com", "pw", is_staff=True)
        self.client.logout()
        self.client.login(username="ed", password="pw")
        html = self.client.get(reverse("desk:dashboard")).content.decode()
        self.assertIn(">Settings</a>", html)
        self.assertNotIn(">Editor accounts</a>", html)

    def test_the_old_listings_path_forwards_to_events(self):
        response = self.client.get("/desk/listings/?status=draft")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/desk/events/?status=draft")

    def test_the_health_panel_moved_to_settings(self):
        overview = self.client.get(reverse("desk:dashboard"))
        self.assertNotContains(overview, "Secret key")
        settings_page = self.client.get(reverse("desk:siteconfig"))
        self.assertContains(settings_page, "Secret key")

    def test_email_designs_live_under_settings(self):
        self.assertEqual(reverse("desk:templates_list"), "/desk/email-designs/")
        self.assertEqual(self.client.get(reverse("desk:templates_list")).status_code, 200)
        self.assertContains(self.client.get(reverse("desk:siteconfig")),
                            reverse("desk:templates_list"))
        self.assertEqual(self.client.get("/desk/templates/").status_code, 404)






class CampaignFromIdeaTests(DeskCase):
    def setUp(self):
        super().setUp()
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        Reader.objects.create(email="ada@example.com", location="London",
                              interest_categories=["music"]).interest_tags.add(self.jazz)

    def test_the_idea_becomes_a_full_draft_you_land_on(self):
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.draft_campaign", return_value={
                 "name": "Frieze weekend", "subject": "Frieze, {first_name}",
                 "brief": "Frieze is on. Free Sunday.", "body": "Go on Sunday.",
                 "link_label": "Plan the day", "tags": ["jazz-nights", "nope"],
                 "categories": ["music", "nope"], "location": "London"}):
            response = self.client.post(reverse("desk:campaigns_from_idea"),
                                        {"idea": "Frieze is on this weekend"}, follow=True)
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.name, "Frieze weekend")
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)
        self.assertEqual(list(campaign.audience_tags.all()), [self.jazz])
        self.assertEqual(campaign.audience_categories, ["music"])
        self.assertEqual(campaign.audience_location, "London")
        self.assertContains(response, "Drafted “Frieze weekend” for 1 reader")
        self.assertEqual(CampaignDelivery.objects.count(), 0)

    def test_without_ai_the_idea_is_kept_as_the_brief(self):
        response = self.client.post(reverse("desk:campaigns_from_idea"),
                                    {"idea": "Frieze is on"}, follow=True)
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.brief, "Frieze is on")
        self.assertContains(response, "the idea is saved as the brief")

    def test_an_empty_idea_creates_nothing(self):
        self.client.post(reverse("desk:campaigns_from_idea"), {"idea": "  "}, follow=True)
        self.assertEqual(Campaign.objects.count(), 0)

