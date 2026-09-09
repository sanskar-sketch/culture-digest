"""Events: they end on their own, they know who they'd suit, and a
campaign can be drafted about one from its own facts."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from campaigns.models import Campaign
from opportunities import research
from opportunities.models import Opportunity, Tag
from readers.models import Reader

from .tests import plain_static


def event(title="Gig", **kw):
    data = dict(title=title, category="music", description="A trio in a basement.",
                price_tier="budget", location_area="London",
                booking_url="https://example.com/book", mainstream_to_unusual=3,
                intimate_to_large_scale=2, status=Opportunity.Status.PUBLISHED)
    data.update(kw)
    return Opportunity.objects.create(**data)


class ArchiveOnEndTests(TestCase):
    def setUp(self):
        cache.clear()
        self.today = timezone.localdate()

    def test_a_published_event_that_has_ended_is_archived(self):
        gone = event("Over", end_date=self.today - timedelta(days=1))
        on = event("Still on", end_date=self.today + timedelta(days=3))
        ends_today = event("Last day", end_date=self.today)
        no_end = event("Ongoing")
        self.assertEqual(Opportunity.archive_ended(), 1)
        for e, status in ((gone, "archived"), (on, "published"),
                          (ends_today, "published"), (no_end, "published")):
            e.refresh_from_db()
            self.assertEqual(e.status, status, e.title)

    def test_a_draft_that_has_ended_is_left_for_the_editor(self):
        """Archiving a draft would hide it from the person deciding on it."""
        e = event("Never published", status=Opportunity.Status.DRAFT,
                  end_date=self.today - timedelta(days=1))
        Opportunity.archive_ended()
        e.refresh_from_db()
        self.assertEqual(e.status, Opportunity.Status.DRAFT)

    def test_the_scheduler_does_it_and_says_so(self):
        from campaigns.scheduler import run_due

        event("Over", end_date=self.today - timedelta(days=1))
        report = run_due(budget_seconds=5)
        self.assertTrue(any("archived 1" in line for line in report["lines"]))
        self.assertEqual(Opportunity.objects.get().status, Opportunity.Status.ARCHIVED)


class ResearchForReadersTests(TestCase):
    def setUp(self):
        cache.clear()
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.discos = Tag.objects.create(name="Silent discos", slug="silent-discos")
        self.ada = Reader.objects.create(email="ada@example.com", location="London")
        self.bo = Reader.objects.create(email="bo@example.com", location="London")
        self.cy = Reader.objects.create(email="cy@example.com", location="Leeds")
        self.ada.interest_tags.add(self.jazz, self.discos)
        self.bo.interest_tags.add(self.discos)
        self.cy.interest_tags.add(self.jazz)
        event("Trio").tags.add(self.jazz)  # jazz has something; discos has nothing

    def test_it_asks_for_what_readers_have_most_and_you_have_least(self):
        with mock.patch("opportunities.research.start", return_value=True) as start:
            started = research.for_readers(limit=1)
        self.assertEqual(started, ["Silent discos"])
        self.assertEqual(start.call_args.kwargs["area"], "London")

    def test_it_searches_where_most_of_them_are(self):
        with mock.patch("opportunities.research.start", return_value=True) as start:
            research.for_readers(Reader.objects.filter(pk=self.cy.pk))
        self.assertEqual(start.call_args.kwargs["area"], "Leeds")

    def test_nobody_means_nothing_to_search_for(self):
        with mock.patch("opportunities.research.start") as start:
            self.assertEqual(research.for_readers(Reader.objects.none()), [])
        start.assert_not_called()

    def test_interests_nobody_has_are_not_searched(self):
        Tag.objects.create(name="Nobody's", slug="nobodys")
        with mock.patch("opportunities.research.start", return_value=True) as start:
            research.for_readers(limit=10)
        names = [c.args[0].name for c in start.call_args_list]
        self.assertNotIn("Nobody's", names)


@plain_static
class EventScreenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.boss = get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.login(username="boss", password="pw")
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.ada = Reader.objects.create(email="ada@example.com", location="London")
        self.ada.interest_tags.add(self.jazz)
        self.gig = event("Trio residency", end_date=timezone.localdate() + timedelta(days=30))

    def test_the_list_is_called_events_and_offers_suggestions(self):
        page = self.client.get(reverse("desk:listings_list"))
        self.assertContains(page, "<h1>Events</h1>", html=True)
        self.assertContains(page, "Suggest events for my readers")

    def test_who_it_reaches_is_nobody_until_it_has_interests(self):
        page = self.client.get(reverse("desk:listings_change", args=[self.gig.pk]))
        self.assertIsNone(page.context["suits"]["url"])
        self.assertContains(page, "no reader can be matched")

    def test_who_it_reaches_counts_readers_by_its_interests(self):
        self.gig.tags.add(self.jazz)
        page = self.client.get(reverse("desk:listings_change", args=[self.gig.pk]))
        self.assertEqual(page.context["suits"]["count"], 1)
        self.assertIn("tag=jazz-nights", page.context["suits"]["url"])

    def test_who_would_like_this_tags_the_event_from_our_own_interests(self):
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.classify_opportunity", return_value={
                 "category": "music", "tags": ["jazz-nights", "invented"],
                 "price_tier": "budget", "mainstream_to_unusual": 3,
                 "intimate_to_large_scale": 2, "reasoning": ""}):
            response = self.client.post(reverse("desk:listings_who", args=[self.gig.pk]),
                                        follow=True)
        self.assertEqual(list(self.gig.tags.all()), [self.jazz])
        self.assertContains(response, "1 reader would be reached")

    def test_a_campaign_drafted_from_the_event_carries_its_facts(self):
        self.gig.tags.add(self.jazz)
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.draft_campaign", return_value={
                 "name": "Trio this month", "subject": "A trio, {first_name}",
                 "brief": "Trio residency, London, £12.", "body": "Go.",
                 "link_label": "Book", "tags": [], "categories": ["music"],
                 "location": ""}) as draft:
            response = self.client.post(reverse("desk:campaigns_from_event", args=[self.gig.pk]),
                                        {"angle": "for people who like small rooms"}, follow=True)
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.event, self.gig)
        self.assertEqual(campaign.link_url, "https://example.com/book")
        self.assertEqual(list(campaign.audience_tags.all()), [self.jazz])  # fell back to the event's
        self.assertIn("Trio residency", draft.call_args.args[0])
        self.assertIn("small rooms", draft.call_args.args[0])
        self.assertContains(response, "Drafted “Trio this month” about Trio residency")
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)

    def test_without_ai_the_events_facts_become_the_brief(self):
        self.gig.tags.add(self.jazz)
        self.client.post(reverse("desk:campaigns_from_event", args=[self.gig.pk]), follow=True)
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.event, self.gig)
        self.assertIn("https://example.com/book", campaign.brief)
        self.assertEqual(list(campaign.audience_tags.all()), [self.jazz])

    def test_several_campaigns_can_point_at_one_event(self):
        for _ in range(2):
            self.client.post(reverse("desk:campaigns_from_event", args=[self.gig.pk]))
        self.assertEqual(self.gig.campaigns.count(), 2)
        page = self.client.get(reverse("desk:listings_change", args=[self.gig.pk]))
        self.assertEqual(len(page.context["campaigns"]), 2)

    def test_suggest_events_from_the_users_page_uses_the_ticked_readers(self):
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("opportunities.research.for_readers", return_value=["Jazz nights"]) as fr:
            response = self.client.post(reverse("desk:readers_list"),
                                        {"action": "suggest_events", "selected": [self.ada.pk]},
                                        follow=True)
        self.assertEqual(list(fr.call_args.args[0]), [self.ada])
        self.assertContains(response, "Searching for events for Jazz nights")

    def test_suggest_for_my_readers_from_the_events_page(self):
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("opportunities.research.for_readers", return_value=["Jazz nights"]):
            response = self.client.post(reverse("desk:listings_list"),
                                        {"action": "suggest_for_readers"}, follow=True)
        self.assertContains(response, "Searching for events for Jazz nights")

    def test_the_campaign_form_can_name_its_event_but_not_an_archived_one(self):
        from desk.forms import CampaignForm

        over = event("Over", status=Opportunity.Status.ARCHIVED)
        choices = CampaignForm().fields["event"].queryset
        self.assertIn(self.gig, choices)
        self.assertNotIn(over, choices)
