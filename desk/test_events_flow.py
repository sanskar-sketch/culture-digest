"""Events: they end on their own, they know who they'd suit, and a
campaign can be drafted about one from its own facts."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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
        event("Untagged thing")

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





    def test_suggest_for_my_readers_from_the_events_page(self):
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("opportunities.research.for_readers", return_value=["Jazz nights"]):
            response = self.client.post(reverse("desk:listings_list"),
                                        {"action": "suggest_for_readers"}, follow=True)
        self.assertContains(response, "Searching for events for Jazz nights")

    def test_a_new_event_is_tagged_on_creation_when_the_editor_left_it_blank(self):
        data = {"title": "Late set", "slug": "", "category": "music", "status": "draft",
                "description": "x", "editorial_note": "", "price_tier": "budget",
                "price_display": "", "location_name": "", "location_area": "London",
                "booking_url": "https://example.com/late", "start_date": "", "end_date": "",
                "critic_rating": "", "critic_rating_source": "", "critic_quote": "",
                "mainstream_to_unusual": "3", "intimate_to_large_scale": "2"}
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.classify_opportunity", return_value={
                 "category": "music", "tags": ["jazz-nights", "invented"], "price_tier": "budget",
                 "mainstream_to_unusual": 3, "intimate_to_large_scale": 2, "reasoning": ""}):
            response = self.client.post(reverse("desk:listings_add"), data, follow=True)
        made = Opportunity.objects.get(title="Late set")
        self.assertEqual(list(made.tags.all()), [self.jazz])
        self.assertContains(response, "Tagged it: Jazz nights")

    def test_an_editors_own_tags_are_not_overwritten_on_creation(self):
        other = Tag.objects.create(name="Basement rooms", slug="basement-rooms")
        data = {"title": "Late set", "slug": "", "category": "music", "status": "draft",
                "description": "x", "editorial_note": "", "price_tier": "budget",
                "price_display": "", "location_name": "", "location_area": "London",
                "booking_url": "https://example.com/late", "start_date": "", "end_date": "",
                "critic_rating": "", "critic_rating_source": "", "critic_quote": "",
                "mainstream_to_unusual": "3", "intimate_to_large_scale": "2",
                "tags": [other.pk]}
        with mock.patch("recommendations.ai.classify_opportunity") as classify:
            self.client.post(reverse("desk:listings_add"), data, follow=True)
        classify.assert_not_called()
        self.assertEqual(list(Opportunity.objects.get(title="Late set").tags.all()), [other])

    def test_fill_in_with_ai_prefills_the_form_and_saves_nothing(self):
        found = {"listings": [{
            "title": "Frieze Sculpture", "description": "Free, in the park.",
            "category": "exhibition", "price_tier": "free", "price_display": "Free",
            "location_name": "Regent's Park", "location_area": "London",
            "booking_url": "https://example.com/frieze", "start_date": "2026-10-01",
            "end_date": "2026-10-31", "mainstream_to_unusual": 2, "intimate_to_large_scale": 4,
            "tags": ["jazz-nights"], "sources": [{"title": "Frieze", "url": "https://frieze.com"}]}],
            "notes": ""}
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.research_listings", return_value=found) as research_call:
            response = self.client.post(reverse("desk:listings_add"),
                                        {"fill_from": "Frieze Sculpture", "fill_area": "London"})
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial["title"], "Frieze Sculpture")
        self.assertEqual(form.initial["price_tier"], "free")
        self.assertEqual(str(form.initial["start_date"]), "2026-10-01")
        self.assertEqual(form.initial["tags"], [self.jazz.pk])
        self.assertIn("https://frieze.com", form.initial["editorial_note"])
        self.assertContains(response, "Filled in from 1 page")
        self.assertFalse(Opportunity.objects.filter(title="Frieze Sculpture").exists())
        # inside a request, so it must not be allowed the background run's patience
        self.assertLessEqual(research_call.call_args.kwargs["timeout"], 25)

    def test_fill_in_with_ai_that_finds_nothing_says_so(self):
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.research_listings", return_value={"listings": []}):
            response = self.client.post(reverse("desk:listings_add"), {"fill_from": "Nonsense"})
        self.assertContains(response, "Couldn")
        self.assertEqual(Opportunity.objects.count(), 2)  # only setUp's

    def test_the_list_says_who_each_event_is_good_for(self):
        self.gig.tags.add(self.jazz)
        page = self.client.get(reverse("desk:listings_list"))
        self.assertContains(page, "1 user")
        self.assertContains(page, "tag=jazz-nights")
        self.assertContains(page, "nobody yet")  # an untagged event

    def test_the_count_opens_exactly_those_users_on_the_page(self):
        """The number and the names have to be the same people: an inferred
        interest counts like a picked one, and someone unsubscribed counts
        as neither."""
        self.gig.tags.add(self.jazz)
        bob = Reader.objects.create(email="bob@example.com", name="Bob")
        bob.ai_inferred_tags.add(self.jazz)
        gone = Reader.objects.create(email="gone@example.com", is_active=False)
        gone.interest_tags.add(self.jazz)

        page = self.client.get(reverse("desk:listings_list"))
        gig = [o for o in page.context["page_obj"] if o.pk == self.gig.pk][0]
        self.assertEqual(gig.good_for, 2)
        self.assertEqual([row["reader"].email for row in gig.suits],
                         ["ada@example.com", "bob@example.com"])
        self.assertEqual(gig.suits[0]["picked"], ["Jazz nights"])
        self.assertEqual(gig.suits[1]["inferred"], ["Jazz nights"])
        self.assertEqual(gig.suits_extra, 0)

        self.assertContains(page, "2 users")
        self.assertContains(page, f'id="suits-{self.gig.pk}"')
        self.assertContains(page, "bob@example.com")
        self.assertNotContains(page, "gone@example.com")
        self.assertIn(f"r={self.ada.pk},{bob.pk}", gig.send_url)

    def test_naming_them_does_not_cost_a_query_per_event(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries():
            cache.clear()
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(
                    self.client.get(reverse("desk:listings_list")).status_code, 200)
            return len(ctx.captured_queries)

        self.gig.tags.add(self.jazz)
        for i in range(3):
            event(f"More {i}").tags.add(self.jazz)
        queries()
        few = queries()
        for i in range(12):
            event(f"Even more {i}").tags.add(self.jazz)
        self.assertEqual(few, queries())
