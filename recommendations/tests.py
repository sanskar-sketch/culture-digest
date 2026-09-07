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


def make_tag(name, **overrides):
    """Tags in the onboarding taxonomy are seeded by a data migration, so
    tests have to reuse an existing tag rather than clash with it."""
    tag, _ = Tag.objects.get_or_create(name=name, defaults=overrides)
    return tag


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
        jazz = make_tag("jazz")
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
        comedy = make_tag("comedy")

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
        comedy = make_tag("comedy")
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
        jazz = make_tag("jazz")

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


class OptionalProfileFieldsTests(TestCase):
    """Every onboarding field except email is optional - matching must not
    blow up, and should be permissive rather than excluding, when a reader
    hasn't stated a preference."""

    def test_reader_with_no_preferences_at_all_still_gets_matches(self):
        reader = Reader.objects.create(email="blank@example.com")
        make_opportunity(price_tier=Opportunity.PriceTier.SPLURGE, location_area="Tokyo")

        matches = matching.top_matches_for_reader(reader)

        self.assertEqual(len(matches), 1)

    def test_blank_budget_is_not_penalised(self):
        reader = make_reader(budget="")
        cheap = make_opportunity(title="Cheap", price_tier=Opportunity.PriceTier.BUDGET)
        splurge = make_opportunity(title="Splurge", price_tier=Opportunity.PriceTier.SPLURGE)

        matches = {m.opportunity: m.score for m in matching.top_matches_for_reader(reader)}

        self.assertEqual(matches[cheap], matches[splurge])

    def test_blank_location_matches_everything(self):
        reader = make_reader(location="", travel_radius=Reader.TravelRadius.LOCAL_ONLY)
        opportunity = make_opportunity(location_area="Anywhere but here")

        matches = matching.top_matches_for_reader(reader)

        self.assertIn(opportunity, [m.opportunity for m in matches])

    def test_blank_travel_radius_is_permissive(self):
        reader = make_reader(location="Bristol", travel_radius="")
        opportunity = make_opportunity(location_area="London")

        matches = matching.top_matches_for_reader(reader)

        self.assertIn(opportunity, [m.opportunity for m in matches])

    def test_none_mainstream_and_scale_preference_do_not_crash_or_penalise(self):
        reader = make_reader(mainstream_preference=None, scale_preference=None)
        niche = make_opportunity(
            title="Niche", mainstream_to_unusual=5, intimate_to_large_scale=1
        )
        mainstream = make_opportunity(
            title="Mainstream", mainstream_to_unusual=1, intimate_to_large_scale=5
        )

        matches = {m.opportunity: m.score for m in matching.top_matches_for_reader(reader)}

        self.assertEqual(matches[niche], matches[mainstream])


class CategoryAffinityTests(TestCase):
    def test_opportunity_in_a_followed_category_scores_higher(self):
        reader = make_reader(interest_categories=["music"])
        music = make_opportunity(title="Gig", category=Opportunity.Category.MUSIC)
        talk = make_opportunity(title="Lecture", category=Opportunity.Category.TALK)

        scores = {m.opportunity: m.score for m in matching.top_matches_for_reader(reader)}

        self.assertGreater(scores[music], scores[talk])

    def test_category_match_is_explained_to_the_reader(self):
        reader = make_reader(interest_categories=["food"])
        make_opportunity(title="Tasting menu", category=Opportunity.Category.FOOD)

        match = matching.top_matches_for_reader(reader)[0]

        self.assertTrue(any("food" in reason.lower() for reason in match.reasons))

    def test_no_categories_picked_means_no_preference(self):
        reader = make_reader(interest_categories=[])
        music = make_opportunity(title="Gig", category=Opportunity.Category.MUSIC)
        talk = make_opportunity(title="Lecture", category=Opportunity.Category.TALK)

        scores = {m.opportunity: m.score for m in matching.top_matches_for_reader(reader)}

        self.assertEqual(scores[music], scores[talk])

    def test_specific_tag_match_outranks_a_mere_category_match(self):
        jazz = make_tag("jazz", category="music")
        reader = make_reader(interest_categories=["music", "film"])
        reader.interest_tags.add(jazz)

        tagged = make_opportunity(title="Jazz night", category=Opportunity.Category.MUSIC)
        tagged.tags.add(jazz)
        category_only = make_opportunity(title="Film night", category=Opportunity.Category.FILM)

        scores = {m.opportunity: m.score for m in matching.top_matches_for_reader(reader)}

        self.assertGreater(scores[tagged], scores[category_only])


class WildcardTests(TestCase):
    def test_open_to_surprise_swaps_in_an_unrelated_pick(self):
        reader = make_reader(open_to_surprise=True)
        jazz = make_tag("jazz")
        reader.interest_tags.add(jazz)

        # Four on-taste picks fill every slot ahead of the wildcard...
        for i in range(4):
            opp = make_opportunity(title=f"Jazz thing {i}")
            opp.tags.add(jazz)

        # ...and one unrelated, untagged opportunity waiting in the wings.
        wildcard_candidate = make_opportunity(title="Pottery class")

        matches = matching.top_matches_for_reader(reader, limit=4)

        titles = [m.opportunity.title for m in matches]
        self.assertIn("Pottery class", titles)
        wildcard_match = next(m for m in matches if m.opportunity == wildcard_candidate)
        self.assertTrue(any("wildcard" in reason for reason in wildcard_match.reasons))

    def test_reader_not_open_to_surprise_gets_no_wildcard(self):
        reader = make_reader(open_to_surprise=False)
        jazz = make_tag("jazz")
        reader.interest_tags.add(jazz)

        for i in range(4):
            opp = make_opportunity(title=f"Jazz thing {i}")
            opp.tags.add(jazz)
        make_opportunity(title="Pottery class")

        matches = matching.top_matches_for_reader(reader, limit=4)

        titles = [m.opportunity.title for m in matches]
        self.assertNotIn("Pottery class", titles)
