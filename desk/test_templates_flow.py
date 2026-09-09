"""The three-entry sidebar, and what left it."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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

    def test_the_sidebar_has_exactly_the_three_sections(self):
        html = self.client.get(reverse("desk:dashboard")).content.decode()
        nav = html.split('<div class="d-nav">', 1)[1].split('<div class="d-side-foot">', 1)[0]
        for name in ("Events", "Users", "Insights"):
            self.assertIn(f">{name}</a>", nav)
        for gone in ("Listings", "Interests", "Campaigns", "Templates", "Send by interest",
                     "Newsletter issues", "Recommendations", "Email templates",
                     "Site configuration", "Groups"):
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

    def test_interests_are_managed_from_settings(self):
        page = self.client.get(reverse("desk:siteconfig"))
        self.assertContains(page, reverse("desk:interests_list"))
        self.assertEqual(self.client.get(reverse("desk:interests_list")).status_code, 200)

    def test_campaign_pages_are_gone(self):
        self.assertEqual(self.client.get("/desk/campaigns/").status_code, 404)
