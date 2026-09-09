"""The new admin flow: interests in, listings found, mail out, insights back.

The features here all lean on AI, and AI is mocked throughout - not to
avoid the cost, but because what needs testing is our half of the
contract: that a model's answer is checked before it is trusted, that a
listing with no source never lands, that a preview leaves nothing behind,
and that a click is recorded without becoming an open redirect.
"""

from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from campaigns.models import Campaign, CampaignDelivery
from opportunities import research
from opportunities.models import Opportunity, Tag
from readers import interests as reader_interests
from readers.models import Reader
from recommendations.models import LinkClick, NewsletterIssue, Recommendation

from .tests import plain_static


def source(url="https://example.com/gig"):
    return [{"title": "Listings page", "url": url}]


def researched(**overrides):
    row = {
        "title": "Trio residency", "description": "A jazz trio, weekly.",
        "category": "music", "price_tier": "budget", "price_display": "£12",
        "location_name": "The Vortex", "location_area": "London",
        "booking_url": "https://example.com/gig", "start_date": "2026-10-01",
        "end_date": "", "mainstream_to_unusual": 4, "intimate_to_large_scale": 2,
        "tags": [], "sources": source(),
    }
    row.update(overrides)
    return row


@plain_static
class DeskTestCase(TestCase):
    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser("boss", "boss@example.com", "pw")
        self.client.login(username="boss", password="pw")


class ResearchSafetyTests(TestCase):
    """The one place facts arrive from outside, so the one place we check."""

    def setUp(self):
        cache.clear()
        self.interest = Tag.objects.create(name="Late sets", slug="late-sets")

    def test_a_listing_with_no_source_is_dropped_not_saved(self):
        payload = {"listings": [researched(sources=[]), researched(title="Kept")],
                   "notes": ""}
        created = research.save_drafts(payload, interest=self.interest)
        self.assertEqual([o.title for o in created], ["Kept"])

    def test_everything_researched_lands_as_a_draft(self):
        created = research.save_drafts({"listings": [researched()]}, interest=self.interest)
        listing = created[0]
        self.assertEqual(listing.status, Opportunity.Status.DRAFT)
        self.assertTrue(listing.found_by_ai)
        self.assertEqual(listing.sources, source())
        # and it carries the interest it was found for
        self.assertIn(self.interest, listing.tags.all())

    def test_a_made_up_category_or_price_falls_back_rather_than_saving(self):
        created = research.save_drafts(
            {"listings": [researched(category="jazz-night", price_tier="cheapish")]})
        self.assertEqual(created[0].category, "other")
        self.assertEqual(created[0].price_tier, Opportunity.PriceTier.MODERATE)

    def test_an_unparseable_date_becomes_blank_rather_than_wrong(self):
        created = research.save_drafts(
            {"listings": [researched(start_date="next Tuesday", end_date="")]})
        self.assertIsNone(created[0].start_date)

    def test_dials_are_clamped_to_the_scale_they_are_scored_on(self):
        created = research.save_drafts(
            {"listings": [researched(mainstream_to_unusual=9,
                                     intimate_to_large_scale=0)]})
        self.assertEqual(created[0].mainstream_to_unusual, 5)
        self.assertEqual(created[0].intimate_to_large_scale, 1)

    def test_something_we_already_have_is_not_added_twice(self):
        research.save_drafts({"listings": [researched()]})
        again = research.save_drafts({"listings": [researched()]})
        self.assertEqual(again, [])
        self.assertEqual(Opportunity.objects.count(), 1)

    def test_a_missing_booking_link_falls_back_to_the_source(self):
        created = research.save_drafts({"listings": [researched(booking_url="")]})
        self.assertEqual(created[0].booking_url, "https://example.com/gig")

    def test_research_that_fails_creates_nothing_and_says_so(self):
        with mock.patch("recommendations.ai.research_listings", return_value=None):
            result = research.run(self.interest)
        self.assertFalse(result["ok"])
        self.assertEqual(Opportunity.objects.count(), 0)


class TypedInterestTests(TestCase):
    """What a reader types stops being a dead field."""

    def setUp(self):
        cache.clear()
        self.reader = Reader.objects.create(
            email="ada@example.com", other_interests="silent discos, letterpress")

    def test_a_new_interest_is_created_and_attributed_to_readers(self):
        with mock.patch("recommendations.ai.propose_interests", return_value=[
                {"name": "Silent discos", "category": "music"}]):
            reader_interests.absorb(self.reader)
        tag = Tag.objects.get(slug="silent-discos")
        self.assertEqual(tag.origin, Tag.Origin.READER)
        self.assertEqual(tag.times_requested, 1)
        # and it counts as one of theirs, because they asked for it
        self.assertIn(tag, self.reader.interest_tags.all())

    def test_a_second_reader_asking_counts_rather_than_duplicates(self):
        other = Reader.objects.create(email="bo@example.com")
        with mock.patch("recommendations.ai.propose_interests", return_value=[
                {"name": "Silent discos", "category": "music"}]):
            reader_interests.absorb(self.reader)
            reader_interests.absorb(other)
        self.assertEqual(Tag.objects.filter(slug="silent-discos").count(), 1)
        self.assertEqual(Tag.objects.get(slug="silent-discos").times_requested, 2)

    def test_an_editors_interest_stays_an_editors_interest(self):
        Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        with mock.patch("recommendations.ai.propose_interests", return_value=[
                {"name": "Jazz nights", "category": "music"}]):
            reader_interests.absorb(self.reader)
        tag = Tag.objects.get(slug="jazz-nights")
        self.assertEqual(tag.origin, Tag.Origin.EDITOR)
        self.assertEqual(tag.times_requested, 1)

    def test_a_made_up_category_is_refused(self):
        with mock.patch("recommendations.ai.propose_interests", return_value=[
                {"name": "Silent discos", "category": "not-a-category"}]):
            reader_interests.absorb(self.reader)
        self.assertEqual(Tag.objects.get(slug="silent-discos").category, "")


class InterestQueueTests(DeskTestCase):
    def test_the_page_names_what_readers_want_and_we_cannot_send(self):
        Tag.objects.create(name="Silent discos", slug="silent-discos",
                           origin=Tag.Origin.READER, times_requested=3)
        page = self.client.get(reverse("desk:interests_list"))
        self.assertContains(page, "readers asked for")
        self.assertContains(page, "Silent discos")

    def test_what_readers_asked_for_comes_first(self):
        Tag.objects.create(name="Editor's pick", slug="editors-pick")
        Tag.objects.create(name="Silent discos", slug="silent-discos",
                           origin=Tag.Origin.READER, times_requested=3)
        page = self.client.get(reverse("desk:interests_list"))
        self.assertEqual(page.context["page_obj"][0].slug, "silent-discos")

    def test_research_is_started_off_the_request_not_during_it(self):
        tag = Tag.objects.create(name="Silent discos", slug="silent-discos")
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("opportunities.research.start", return_value=True) as start:
            response = self.client.post(reverse("desk:interests_list"),
                                        {"action": "research", "selected": [tag.pk],
                                         "area": "London"}, follow=True)
        start.assert_called_once()
        self.assertEqual(start.call_args.kwargs["area"], "London")
        self.assertIn("Searching for Silent discos",
                      " ".join(m.message for m in response.context["messages"]))

    def test_without_ai_it_says_so_rather_than_doing_nothing(self):
        tag = Tag.objects.create(name="Silent discos", slug="silent-discos")
        response = self.client.post(reverse("desk:interests_list"),
                                    {"action": "research", "selected": [tag.pk]},
                                    follow=True)
        self.assertIn("AI is not configured",
                      " ".join(m.message for m in response.context["messages"]))


class PickByInterestTests(DeskTestCase):
    """The Users page: pick interests, see exactly who has them, send."""

    def setUp(self):
        super().setUp()
        self.tag = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.ada = Reader.objects.create(email="ada@example.com", location="London",
                                         interest_categories=["music"])
        self.ada.interest_tags.add(self.tag)
        self.bo = Reader.objects.create(email="bo@example.com", location="London")
        for i in range(3):
            listing = Opportunity.objects.create(
                title=f"Gig {i}", category="music", description="x", price_tier="budget",
                location_area="London", booking_url=f"https://example.com/{i}",
                mainstream_to_unusual=3, intimate_to_large_scale=2,
                status=Opportunity.Status.PUBLISHED)
            listing.tags.add(self.tag)

    def url(self, query=""):
        return reverse("desk:readers_list") + (f"?{query}" if query else "")

    def test_picking_an_interest_shows_exactly_who_has_it(self):
        page = self.client.get(self.url("tag=jazz-nights"))
        self.assertEqual(page.context["result_count"], 1)
        self.assertContains(page, "ada@example.com")
        self.assertNotContains(page, "bo@example.com")

    def test_an_inferred_interest_counts_too(self):
        self.bo.ai_inferred_tags.add(self.tag)
        page = self.client.get(self.url("tag=jazz-nights"))
        self.assertEqual(page.context["result_count"], 2)

    def test_match_all_narrows_rather_than_widens(self):
        other = Tag.objects.create(name="Basement rooms", slug="basement-rooms")
        self.bo.interest_tags.add(self.tag, other)
        wide = self.client.get(self.url("tag=jazz-nights&tag=basement-rooms"))
        self.assertEqual(wide.context["result_count"], 2)
        narrow = self.client.get(self.url("tag=jazz-nights&tag=basement-rooms&match=all"))
        self.assertEqual(narrow.context["result_count"], 1)


    def test_sending_reaches_only_the_readers_ticked(self):
        response = self.client.post(self.url("tag=jazz-nights"), {
            "action": "send", "selected": [self.ada.pk]}, follow=True)
        self.assertIn("Sent", " ".join(m.message for m in response.context["messages"]))
        self.assertEqual(NewsletterIssue.objects.count(), 1)
        self.assertEqual(NewsletterIssue.objects.get().reader, self.ada)

    def test_every_tag_of_a_reader_is_on_one_page(self):
        inferred = Tag.objects.create(name="Basement rooms", slug="basement-rooms")
        avoid = Tag.objects.create(name="Arena shows", slug="arena-shows")
        self.ada.ai_inferred_tags.add(inferred)
        self.ada.ai_avoid_tags.add(avoid)
        page = self.client.get(reverse("desk:reader_tags", args=[self.ada.pk]))
        for name in ("Jazz nights", "Basement rooms", "Arena shows"):
            self.assertContains(page, name)


class ClickTrackingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.reader = Reader.objects.create(email="ada@example.com")
        self.listing = Opportunity.objects.create(
            title="Gig", category="music", description="x", price_tier="budget",
            location_area="London", booking_url="https://example.com/book",
            mainstream_to_unusual=3, intimate_to_large_scale=2,
            status=Opportunity.Status.PUBLISHED)
        issue = NewsletterIssue.objects.create(reader=self.reader)
        self.rec = Recommendation.objects.create(
            issue=issue, opportunity=self.listing, rationale="x")

    def test_a_booking_click_is_recorded_and_forwards_to_the_listing(self):
        response = self.client.get(
            reverse("recommendations:booking-click", args=[self.rec.feedback_token]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "https://example.com/book")
        click = LinkClick.objects.get()
        self.assertEqual(click.reader, self.reader)
        self.assertEqual(click.recommendation, self.rec)
        self.assertEqual(click.section, LinkClick.Section.BOOKING)

    def test_the_destination_cannot_be_supplied_by_the_visitor(self):
        """No ?next= to abuse: it comes from the listing, every time."""
        response = self.client.get(
            reverse("recommendations:booking-click", args=[self.rec.feedback_token]),
            {"next": "https://evil.example.com"})
        self.assertEqual(response.url, "https://example.com/book")

    def test_an_unknown_token_is_a_404_not_a_redirect(self):
        import uuid

        response = self.client.get(
            reverse("recommendations:booking-click", args=[uuid.uuid4()]))
        self.assertEqual(response.status_code, 404)

    def test_pressing_a_feedback_button_is_recorded_as_a_click_too(self):
        self.client.get(reverse("recommendations:feedback",
                                args=[self.rec.feedback_token, "booked"]))
        self.assertEqual(LinkClick.objects.get().section, LinkClick.Section.FEEDBACK)


class RepeatingCampaignTests(TestCase):
    def setUp(self):
        cache.clear()
        self.today = timezone.localdate()
        self.campaign = Campaign.objects.create(
            name="Weekly bulletin", subject="This week", brief="Things on.",
            status=Campaign.Status.SENT,
            frequency=Campaign.Frequency.WEEKLY,
            starts_on=self.today - timedelta(days=14), send_hour=0)

    def test_a_one_off_campaign_never_repeats(self):
        self.campaign.frequency = Campaign.Frequency.ONCE
        due, why = self.campaign.due_for_a_run()
        self.assertFalse(due)
        self.assertIn("Not a repeating", why)

    def test_it_waits_for_its_start_date(self):
        self.campaign.starts_on = self.today + timedelta(days=3)
        due, why = self.campaign.due_for_a_run()
        self.assertFalse(due)
        self.assertIn("Starts on", why)

    def test_it_stops_after_its_end_date(self):
        self.campaign.ends_on = self.today - timedelta(days=1)
        due, why = self.campaign.due_for_a_run()
        self.assertFalse(due)
        self.assertIn("Finished on", why)

    def test_it_does_not_run_twice_in_one_day(self):
        self.campaign.last_run_on = self.today
        due, why = self.campaign.due_for_a_run()
        self.assertFalse(due)
        self.assertIn("Already run today", why)

    def test_weekly_waits_a_week(self):
        self.campaign.last_run_on = self.today - timedelta(days=3)
        self.assertFalse(self.campaign.due_for_a_run()[0])
        self.campaign.last_run_on = self.today - timedelta(days=8)
        self.assertTrue(self.campaign.due_for_a_run()[0])

    def test_a_cancelled_repeat_stays_cancelled(self):
        self.campaign.status = Campaign.Status.CANCELLED
        self.assertFalse(self.campaign.due_for_a_run()[0])

    def test_a_run_puts_the_whole_audience_back_in_the_queue(self):
        reader = Reader.objects.create(email="ada@example.com")
        CampaignDelivery.objects.create(campaign=self.campaign, reader=reader,
                                        status=CampaignDelivery.Status.SENT,
                                        sent_at=timezone.now())
        self.campaign.begin_repeat_run()
        delivery = CampaignDelivery.objects.get()
        self.assertEqual(delivery.status, CampaignDelivery.Status.PENDING)
        self.assertIsNone(delivery.sent_at)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.SCHEDULED)
        self.assertEqual(self.campaign.last_run_on, self.today)

    def test_the_scheduler_requeues_it(self):
        from campaigns.scheduler import start_repeats

        lines = start_repeats()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, Campaign.Status.SCHEDULED)
        self.assertTrue(any("Weekly bulletin" in line for line in lines))


class AudienceSuggestionTests(DeskTestCase):
    def test_the_suggestion_is_applied_and_only_from_our_own_tables(self):
        real = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        campaign = Campaign.objects.create(name="Frieze", subject="s", brief="b")
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.suggest_audience", return_value={
                 "tags": ["jazz-nights", "invented-slug"],
                 "categories": ["music", "not-a-category"],
                 "location": "London", "reasoning": "It's a music thing."}):
            self.client.post(reverse("desk:campaigns_suggest_audience", args=[campaign.pk]),
                             follow=True)
        campaign.refresh_from_db()
        self.assertEqual(list(campaign.audience_tags.all()), [real])
        self.assertEqual(campaign.audience_categories, ["music"])
        self.assertEqual(campaign.audience_location, "London")

    def test_it_sends_nothing(self):
        campaign = Campaign.objects.create(name="Frieze", subject="s", brief="b")
        Reader.objects.create(email="ada@example.com")
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.suggest_audience", return_value={
                 "tags": [], "categories": [], "location": "", "reasoning": ""}):
            self.client.post(reverse("desk:campaigns_suggest_audience", args=[campaign.pk]),
                             follow=True)
        self.assertEqual(CampaignDelivery.objects.count(), 0)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)


class InsightsTests(DeskTestCase):
    def test_it_reports_what_readers_want_and_what_they_opened(self):
        tag = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        wanted = Tag.objects.create(name="Silent discos", slug="silent-discos",
                                    origin=Tag.Origin.READER, times_requested=4)
        reader = Reader.objects.create(email="ada@example.com",
                                       interest_categories=["music"])
        reader.interest_tags.add(tag)
        listing = Opportunity.objects.create(
            title="Gig", category="music", description="x", price_tier="budget",
            location_area="London", booking_url="https://example.com/book",
            mainstream_to_unusual=3, intimate_to_large_scale=2,
            status=Opportunity.Status.PUBLISHED)
        issue = NewsletterIssue.objects.create(reader=reader, sent_at=timezone.now())
        rec = Recommendation.objects.create(issue=issue, opportunity=listing, rationale="x")
        LinkClick.objects.create(reader=reader, recommendation=rec, url="x",
                                 section=LinkClick.Section.BOOKING)

        page = self.client.get(reverse("desk:insights"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Jazz nights")
        self.assertContains(page, "Silent discos")   # asked for, nothing to send
        self.assertContains(page, "Gig")             # most opened
        self.assertEqual(page.context["click_rate"], 100)
        self.assertEqual(page.context["readers_who_clicked"], 1)

    def test_it_renders_on_an_empty_database(self):
        page = self.client.get(reverse("desk:insights"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["click_rate"], 0)

    def test_an_editor_can_reach_it_and_a_stranger_cannot(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("desk:insights")).status_code, 302)
