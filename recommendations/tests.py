from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from opportunities.models import Opportunity, Tag
from readers.models import Reader
from recommendations import matching
from recommendations.models import NewsletterIssue, Recommendation


def make_opportunity(**overrides):
    defaults = dict(
        title="Test opportunity",
        category=Opportunity.Category.MUSIC,
        description="A thing.",
        price_tier=Opportunity.PriceTier.MODERATE,
        location_area="London",
        booking_url="https://example.com/book",
        mainstream_to_unusual=3,
        intimate_to_large_scale=3,
        status=Opportunity.Status.PUBLISHED,
    )
    defaults.update(overrides)
    return Opportunity.objects.create(**defaults)


def make_reader(**overrides):
    defaults = dict(
        email="reader@example.com",
        location="London",
        travel_radius=Reader.TravelRadius.WITHIN_CITY,
        budget=Reader.Budget.MODERATE,
        mainstream_preference=3,
        scale_preference=3,
    )
    defaults.update(overrides)
    return Reader.objects.create(**defaults)


class BudgetMatchingTests(TestCase):
    def test_splurge_item_excluded_for_free_cheap_budget(self):
        reader = make_reader(budget=Reader.Budget.FREE_CHEAP)
        opportunity = make_opportunity(price_tier=Opportunity.PriceTier.SPLURGE)

        matches = matching.top_matches_for_reader(reader)

        self.assertNotIn(opportunity, [m.opportunity for m in matches])

    def test_free_item_always_included_regardless_of_budget(self):
        reader = make_reader(budget=Reader.Budget.NO_LIMIT)
        opportunity = make_opportunity(price_tier=Opportunity.PriceTier.FREE)

        matches = matching.top_matches_for_reader(reader)

        self.assertIn(opportunity, [m.opportunity for m in matches])


class LocationMatchingTests(TestCase):
    def test_local_only_reader_excludes_other_cities(self):
        reader = make_reader(travel_radius=Reader.TravelRadius.LOCAL_ONLY, location="Bristol")
        opportunity = make_opportunity(location_area="London")

        matches = matching.top_matches_for_reader(reader)

        self.assertNotIn(opportunity, [m.opportunity for m in matches])

    def test_online_opportunity_always_matches_location(self):
        reader = make_reader(travel_radius=Reader.TravelRadius.LOCAL_ONLY, location="Bristol")
        opportunity = make_opportunity(location_area="London", is_online=True)

        matches = matching.top_matches_for_reader(reader)

        self.assertIn(opportunity, [m.opportunity for m in matches])

    def test_anywhere_reader_sees_out_of_area_items_with_penalty(self):
        reader = make_reader(travel_radius=Reader.TravelRadius.ANYWHERE, location="Bristol")
        local = make_opportunity(title="Local", location_area="Bristol")
        far = make_opportunity(title="Far", location_area="London")

        matches = {m.opportunity: m.score for m in matching.top_matches_for_reader(reader)}

        self.assertIn(local, matches)
        self.assertIn(far, matches)
        self.assertGreater(matches[local], matches[far])


class TagAndFeedbackTests(TestCase):
    def test_shared_interest_tags_increase_score(self):
        reader = make_reader()
        jazz = Tag.objects.create(name="jazz")
        reader.interest_tags.add(jazz)

        tagged = make_opportunity(title="Jazz night")
        tagged.tags.add(jazz)
        untagged = make_opportunity(title="Something else")

        matches = {m.opportunity: m.score for m in matching.top_matches_for_reader(reader)}

        self.assertGreater(matches[tagged], matches[untagged])

    def test_single_not_for_me_feedback_down_ranks_but_does_not_exclude(self):
        # A single dislike is a soft signal - down-rank the cluster, but
        # don't permanently blacklist it off one click.
        reader = make_reader()
        comedy = Tag.objects.create(name="comedy")

        disliked = make_opportunity(title="Disliked comedy night")
        disliked.tags.add(comedy)
        issue = NewsletterIssue.objects.create(reader=reader)
        Recommendation.objects.create(
            issue=issue, opportunity=disliked, rationale="x",
            feedback=Recommendation.Feedback.NOT_FOR_ME, feedback_at=timezone.now(),
        )

        another_comedy_night = make_opportunity(title="Another comedy night")
        another_comedy_night.tags.add(comedy)
        unrelated = make_opportunity(title="Unrelated")

        matches = {m.opportunity: m.score for m in matching.top_matches_for_reader(reader)}

        self.assertLess(matches[another_comedy_night], matches[unrelated])

    def test_repeated_not_for_me_feedback_excludes_future_matches(self):
        # A strong, repeated dislike signal should drop the cluster entirely.
        reader = make_reader()
        comedy = Tag.objects.create(name="comedy")
        issue = NewsletterIssue.objects.create(reader=reader)
        for i in range(2):
            disliked = make_opportunity(title=f"Disliked comedy night {i}")
            disliked.tags.add(comedy)
            Recommendation.objects.create(
                issue=issue, opportunity=disliked, rationale="x",
                feedback=Recommendation.Feedback.NOT_FOR_ME, feedback_at=timezone.now(),
            )

        another_comedy_night = make_opportunity(title="Another comedy night")
        another_comedy_night.tags.add(comedy)

        matches = [m.opportunity for m in matching.top_matches_for_reader(reader)]

        self.assertNotIn(another_comedy_night, matches)

    def test_more_like_this_feedback_boosts_similar_future_matches(self):
        reader = make_reader()
        jazz = Tag.objects.create(name="jazz")

        liked = make_opportunity(title="Liked jazz night")
        liked.tags.add(jazz)
        issue = NewsletterIssue.objects.create(reader=reader)
        Recommendation.objects.create(
            issue=issue, opportunity=liked, rationale="x",
            feedback=Recommendation.Feedback.MORE_LIKE_THIS, feedback_at=timezone.now(),
        )

        another_jazz_night = make_opportunity(title="Another jazz night")
        another_jazz_night.tags.add(jazz)
        unrelated = make_opportunity(title="Unrelated")

        matches = {m.opportunity: m.score for m in matching.top_matches_for_reader(reader)}

        self.assertGreater(matches[another_jazz_night], matches[unrelated])


class CooldownAndExclusionTests(TestCase):
    def test_recently_recommended_opportunity_is_excluded(self):
        reader = make_reader()
        opportunity = make_opportunity()
        issue = NewsletterIssue.objects.create(reader=reader)
        Recommendation.objects.create(issue=issue, opportunity=opportunity, rationale="x")

        matches = matching.top_matches_for_reader(reader)

        self.assertNotIn(opportunity, [m.opportunity for m in matches])

    def test_opportunity_recommended_outside_cooldown_window_is_eligible_again(self):
        reader = make_reader()
        opportunity = make_opportunity()
        issue = NewsletterIssue.objects.create(reader=reader)
        rec = Recommendation.objects.create(issue=issue, opportunity=opportunity, rationale="x")
        Recommendation.objects.filter(pk=rec.pk).update(
            created_at=timezone.now() - timedelta(days=365)
        )

        matches = matching.top_matches_for_reader(reader)

        self.assertIn(opportunity, [m.opportunity for m in matches])

    def test_draft_opportunity_is_excluded(self):
        reader = make_reader()
        opportunity = make_opportunity(status=Opportunity.Status.DRAFT)

        matches = matching.top_matches_for_reader(reader)

        self.assertNotIn(opportunity, [m.opportunity for m in matches])

    def test_expired_opportunity_is_excluded(self):
        reader = make_reader()
        opportunity = make_opportunity(end_date=timezone.localdate() - timedelta(days=1))

        matches = matching.top_matches_for_reader(reader)

        self.assertNotIn(opportunity, [m.opportunity for m in matches])


class RationaleTests(TestCase):
    def test_rationale_prefers_editorial_note_over_description(self):
        opportunity = make_opportunity(
            description="Fallback description.",
            editorial_note="Editorial pitch.",
        )
        match = matching.Match(opportunity=opportunity, score=1.0, reasons=["fits their usual budget"])

        rationale = matching.build_rationale(match)

        self.assertIn("Editorial pitch.", rationale)
        self.assertIn("fits their usual budget", rationale)
