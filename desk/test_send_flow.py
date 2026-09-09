"""The send: tick users, see what suits them, pick, read one, send."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from opportunities.models import Opportunity, Tag
from readers.models import Reader
from recommendations.models import NewsletterIssue

from .tests import plain_static


def event(title, tags=(), **kw):
    data = dict(title=title, category="music", description="x", price_tier="budget",
                location_area="London", booking_url=f"https://example.com/{title}",
                mainstream_to_unusual=3, intimate_to_large_scale=2,
                status=Opportunity.Status.PUBLISHED)
    data.update(kw)
    e = Opportunity.objects.create(**data)
    e.tags.add(*tags)
    return e


@plain_static
class SendFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.login(username="boss", password="pw")
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.food = Tag.objects.create(name="Midnight suppers", slug="midnight-suppers")
        self.ada = Reader.objects.create(email="ada@example.com", location="London",
                                         interest_categories=["music"])
        self.ada.interest_tags.add(self.jazz)
        self.bo = Reader.objects.create(email="bo@example.com", location="London",
                                        interest_categories=["food"])
        self.bo.interest_tags.add(self.food)
        self.gigs = [event(f"Gig {i}", tags=[self.jazz]) for i in range(3)]
        self.supper = event("Supper", tags=[self.food], category="food")
        self.url = reverse("desk:send") + f"?r={self.ada.pk},{self.bo.pk}"

    def test_users_arrive_from_the_users_page_ticked(self):
        response = self.client.post(reverse("desk:readers_list"),
                                    {"action": "choose", "selected": [self.ada.pk, self.bo.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("desk:send"), response.url)
        self.assertIn(str(self.ada.pk), response.url)

    def test_no_users_sends_you_back(self):
        response = self.client.get(reverse("desk:send"))
        self.assertRedirects(response, reverse("desk:readers_list"))

    def test_suggestions_are_the_events_the_matching_would_pick_with_reach(self):
        page = self.client.get(self.url)
        rows = {r["event"].title: r for r in page.context["rows"]}
        self.assertIn("Supper", rows)
        # Supper is offered because it suits Bo. It may sit low in Ada's
        # list too - the matching keeps breadth - but it is not her first pick.
        self.assertIn("bo@example.com", [r.email for r in rows["Supper"]["suits"]])
        from recommendations import matching
        self.assertNotEqual(matching.top_matches_for_reader(self.ada, limit=1)[0].opportunity,
                            self.supper)
        for gig in self.gigs:
            self.assertIn(gig.title, rows)
        # the widest-reaching events come first, pre-ticked
        self.assertTrue(page.context["rows"][0]["preticked"])

    def test_preview_builds_one_users_email_from_the_ticked_events_only(self):
        response = self.client.post(reverse("desk:send"), {
            "r": f"{self.ada.pk},{self.bo.pk}", "action": "preview",
            "preview_reader": self.ada.pk,
            "event": [self.gigs[0].pk, self.gigs[1].pk]})
        preview = response.context["preview"]
        self.assertTrue(preview["ok"], preview.get("message"))
        titles = {p["title"] for p in preview["picks"]}
        self.assertEqual(titles, {"Gig 0", "Gig 1"})
        self.assertEqual(NewsletterIssue.objects.count(), 0)

    def test_send_writes_each_user_their_own_email_from_the_pool(self):
        # Two picks each, from a pool of four, so the choice has to differ.
        from siteconfig.models import SiteConfig
        config = SiteConfig.load()
        config.recommendations_per_send = 2
        config.min_recommendations = 1
        config.save()
        response = self.client.post(reverse("desk:send"), {
            "r": f"{self.ada.pk},{self.bo.pk}", "action": "send",
            "event": [g.pk for g in self.gigs] + [self.supper.pk]}, follow=True)
        self.assertContains(response, "Sent to 2 users")
        ada_issue = NewsletterIssue.objects.get(reader=self.ada)
        bo_issue = NewsletterIssue.objects.get(reader=self.bo)
        ada_titles = set(ada_issue.recommendations.values_list("opportunity__title", flat=True))
        bo_titles = set(bo_issue.recommendations.values_list("opportunity__title", flat=True))
        self.assertNotEqual(ada_titles, bo_titles)   # their own picks, not one broadcast
        self.assertIn("Supper", bo_titles)
        self.assertNotIn("Supper", ada_titles)

    def test_a_user_the_pool_does_not_suit_is_skipped_not_padded(self):
        from siteconfig.models import SiteConfig
        config = SiteConfig.load(); config.min_recommendations = 2; config.save()
        response = self.client.post(reverse("desk:send"), {
            "r": f"{self.ada.pk},{self.bo.pk}", "action": "send",
            "event": [self.supper.pk]}, follow=True)
        said = " ".join(m.message for m in response.context["messages"])
        self.assertIn("ada@example.com: Skipped", said)
        self.assertFalse(NewsletterIssue.objects.filter(reader=self.ada).exists())

    def test_nothing_ticked_sends_nothing(self):
        self.client.post(reverse("desk:send"), {"r": f"{self.ada.pk}", "action": "send"}, follow=True)
        self.assertEqual(NewsletterIssue.objects.count(), 0)

    def test_find_more_with_ai_researches_for_these_users(self):
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("opportunities.research.for_readers", return_value=["Midnight suppers"]) as fr:
            response = self.client.post(reverse("desk:send"),
                                        {"r": f"{self.bo.pk}", "action": "research"}, follow=True)
        self.assertEqual(list(fr.call_args.args[0]), [self.bo])
        self.assertContains(response, "Searching for events for Midnight suppers")

    def test_the_pool_never_overrides_who_it_suits(self):
        """Ticking every event still gives each user their own ranking."""
        from recommendations import matching
        picks = matching.top_matches_for_reader(self.bo, pool=[g.pk for g in self.gigs] + [self.supper.pk])
        self.assertEqual(picks[0].opportunity, self.supper)
