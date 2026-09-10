"""The things a click-through of the whole desk turned up.

Every test here is a bug someone could see: a button that answered
"Nothing selected", a Preview that never previewed, working screens with
no link into them, and a skip message that blamed the wrong thing. They
are gathered in one file so it is obvious what they are for.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from opportunities.models import Opportunity, Tag
from readers.models import Reader
from recommendations.models import NewsletterIssue, Recommendation
from siteconfig.models import SiteConfig

from .tests import plain_static


def event(title="Gig", **kw):
    data = dict(title=title, category="music", description="A trio in a basement.",
                price_tier="budget", location_area="London",
                booking_url="https://example.com/book", mainstream_to_unusual=3,
                intimate_to_large_scale=2, status=Opportunity.Status.PUBLISHED)
    data.update(kw)
    return Opportunity.objects.create(**data)


@plain_static
class DeskTestCase(TestCase):
    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.login(username="boss", password="pw")


class LoadSampleEventsIsGoneTests(DeskTestCase):
    """It was scaffolding for an empty install, and it was broken for long
    enough to prove nobody needed it. The command remains for a new one."""

    def test_the_button_is_not_on_the_events_page(self):
        page = self.client.get(reverse("desk:listings_list"))
        self.assertNotContains(page, "load_sample_catalogue")
        self.assertNotContains(page, "Load sample events")

    def test_posting_it_no_longer_seeds_anything(self):
        before = Opportunity.objects.count()
        self.client.post(reverse("desk:listings_list"),
                         {"load_sample_catalogue": "1"}, follow=True)
        self.assertEqual(Opportunity.objects.count(), before)


class PreviewShowsTheEmailTests(DeskTestCase):
    """A button called Preview newsletter should show the newsletter, not
    describe it in one sentence."""

    def setUp(self):
        super().setUp()
        jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.reader = Reader.objects.create(email="ada@example.com", location="London")
        self.reader.interest_tags.add(jazz)
        for i in range(3):
            event(f"Gig {i}").tags.add(jazz)

    def test_preview_renders_the_email_on_the_page(self):
        response = self.client.post(reverse("desk:readers_change", args=[self.reader.pk]),
                                    {"action": "preview"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["preview"])
        self.assertTrue(response.context["preview"]["ok"])
        self.assertContains(response, "d-preview-frame")
        self.assertContains(response, "Subject:")

    def test_preview_sends_nothing_and_records_nothing(self):
        self.client.post(reverse("desk:readers_change", args=[self.reader.pk]),
                         {"action": "preview"})
        self.assertEqual(NewsletterIssue.objects.count(), 0)
        self.assertEqual(Recommendation.objects.count(), 0)

    def test_a_reader_with_nothing_to_send_is_told_rather_than_shown_a_blank(self):
        with mock.patch("recommendations.matching.top_matches_for_reader",
                        return_value=[]):
            response = self.client.post(reverse("desk:readers_change",
                                                args=[self.reader.pk]),
                                        {"action": "preview"}, follow=True)
        self.assertContains(response, "Skipped")
        self.assertIsNone(response.context["preview"])

    def test_saving_the_form_still_saves(self):
        """The preview branch sits in front of the save branch; the save
        must still work."""
        response = self.client.post(reverse("desk:readers_change", args=[self.reader.pk]),
                                    {"email": "ada2@example.com", "is_active": "on"},
                                    follow=True)
        self.reader.refresh_from_db()
        self.assertEqual(self.reader.email, "ada2@example.com")
        self.assertContains(response, "Saved")


class EveryScreenIsReachableTests(DeskTestCase):
    """Four screens worked perfectly and had no link anywhere in the desk.
    You could only reach them by typing the address."""

    def test_the_overview_leads_to_the_records_behind_its_figures(self):
        page = self.client.get(reverse("desk:dashboard"))
        self.assertContains(page, reverse("desk:recommendations_list"))
        self.assertContains(page, reverse("desk:issues_list"))
        self.assertContains(page, reverse("desk:readers_list"))

    def test_insights_leads_to_the_issues_and_the_picks(self):
        page = self.client.get(reverse("desk:insights"))
        self.assertContains(page, reverse("desk:issues_list"))
        self.assertContains(page, reverse("desk:recommendations_list"))

    def test_editor_accounts_leads_to_groups(self):
        page = self.client.get(reverse("desk:users_list"))
        self.assertContains(page, reverse("desk:groups_list"))

    def test_a_user_leads_to_what_we_believe_about_them(self):
        reader = Reader.objects.create(email="ada@example.com")
        for url in (reverse("desk:readers_change", args=[reader.pk]),
                    reverse("desk:readers_list")):
            self.assertContains(self.client.get(url),
                                reverse("desk:reader_tags", args=[reader.pk]))

    def test_no_desk_url_is_an_orphan(self):
        """The guard that would have caught all four: every page in the URL
        map is linked from a template or reachable from the sidebar."""
        import pathlib
        import re

        from config.dashboard import FOOTER_LINKS, SECTIONS

        names = set(re.findall(r'name="([a-z_]+)"',
                               pathlib.Path("desk/urls.py").read_text()))
        markup = " ".join(p.read_text() for p in pathlib.Path("templates").rglob("*.html"))
        views = " ".join(p.read_text() for p in pathlib.Path("desk").rglob("*.py")
                         if "test" not in p.name)
        in_nav = {row["key"] for section in SECTIONS for row in section["rows"]}
        in_nav |= {row["key"] for row in FOOTER_LINKS}

        # Reached by their own machinery rather than by a link: login and
        # the delete confirmations, which every list builds by hand.
        exempt = {"login", "logout", "password_change", "password_change_done",
                  "delete", "delete_selected", "dashboard"}
        orphans = sorted(
            n for n in names
            if n not in exempt
            and f"desk:{n}" not in markup
            and f"desk:{n}" not in views
            and n.split("_")[0] not in in_nav
            and n not in in_nav
        )
        self.assertEqual(orphans, [], f"no way to click through to: {orphans}")


class SkipMessageNamesTheRealCauseTests(DeskTestCase):
    """"Add more published opportunities" sent an editor off to fix the
    catalogue when the catalogue was fine and the cooldown was holding
    everything back."""

    def setUp(self):
        super().setUp()
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.reader = Reader.objects.create(email="ada@example.com", location="London")
        self.reader.interest_tags.add(self.jazz)

    def result_for(self, **kw):
        from recommendations.sending import send_issue_for_reader

        return send_issue_for_reader(self.reader, dry_run=True,
                                     min_recommendations=99, **kw)

    def test_an_empty_catalogue_says_so(self):
        self.assertIn("Nothing is published", self.result_for().message)

    def test_everything_already_sent_blames_the_cooldown_not_the_catalogue(self):
        gig = event("Gig")
        gig.tags.add(self.jazz)
        issue = NewsletterIssue.objects.create(reader=self.reader)
        Recommendation.objects.create(issue=issue, opportunity=gig, rationale="x", score=1)
        message = self.result_for().message
        self.assertIn("cooldown", message)
        self.assertNotIn("Add more published opportunities", message)

    def test_a_reader_with_no_interests_is_told_that(self):
        event("Gig").tags.add(self.jazz)
        blank = Reader.objects.create(email="blank@example.com")
        from recommendations.sending import send_issue_for_reader

        message = send_issue_for_reader(blank, dry_run=True,
                                        min_recommendations=99).message
        self.assertIn("no interests recorded", message)

    def test_otherwise_it_says_nothing_is_close_enough(self):
        event("Gig")  # published, but untagged, so it can't reach her
        self.assertIn("close enough", self.result_for().message)


class WordingTests(DeskTestCase):
    """One vocabulary. The desk says events, users and Settings; messages
    used to say listings, readers and Site configuration."""

    def test_no_screen_says_site_configuration(self):
        for url in (reverse("desk:siteconfig"), reverse("desk:interests_list"),
                    reverse("desk:templates_list")):
            self.assertNotContains(self.client.get(url), "Site configuration")

    def test_the_delete_page_breadcrumb_says_what_the_sidebar_says(self):
        reader = Reader.objects.create(email="ada@example.com")
        page = self.client.get(reverse("desk:delete", args=["readers", reader.pk]))
        self.assertContains(page, "Users")
        self.assertNotContains(page, ">Readers<")

    def test_editor_accounts_is_not_also_called_users(self):
        page = self.client.get(reverse("desk:users_list"))
        self.assertContains(page, "<h1>Editor accounts</h1>", html=True)

    def test_the_interests_page_offers_events_not_listings(self):
        page = self.client.get(reverse("desk:interests_list"))
        self.assertContains(page, "Find events with AI")
        self.assertNotContains(page, "Find listings with AI")

    def test_an_ai_guard_points_at_settings_not_an_env_var(self):
        opp = event("Gig")
        with mock.patch("recommendations.ai.is_enabled", return_value=False):
            response = self.client.post(reverse("desk:listings_who", args=[opp.pk]),
                                        follow=True)
        self.assertContains(response, "Settings → AI assistance")
        self.assertNotContains(response, "OPENAI_API_KEY")


class ClearFiltersKeepsTheSearchTests(DeskTestCase):
    def test_clearing_filters_does_not_also_empty_the_search_box(self):
        event("Jazz thing")
        page = self.client.get(reverse("desk:listings_list"),
                               {"q": "jazz", "status": "published"})
        self.assertContains(page, "Clear all filters")
        self.assertContains(page, f"{reverse('desk:listings_list')}?q=jazz")


class OneInterestShownOnceTests(DeskTestCase):
    """An interest a reader picked, which AI also inferred from their own
    words, is one interest. The panel listed it twice."""

    def test_an_interest_both_picked_and_inferred_is_listed_once(self):
        jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        gigs = Tag.objects.create(name="Basement gigs", slug="basement-gigs")
        reader = Reader.objects.create(email="ada@example.com")
        reader.interest_tags.add(jazz)
        reader.ai_inferred_tags.add(jazz, gigs)

        page = self.client.get(reverse("desk:readers_list"))
        row = [r for r in page.context["page_obj"] if r.pk == reader.pk][0]
        self.assertEqual([t.slug for t in row.inferred_only], ["basement-gigs"])
        # One pill per belief in the panel: picked in accent, inferred in good.
        panel = page.content.decode().split('class="d-detail"')[1]
        self.assertEqual(panel.count("Jazz nights"), 1)
        self.assertEqual(panel.count("Basement gigs"), 1)


class ResearchNotesOnlyRealLinksTests(DeskTestCase):
    """AI occasionally writes the word "source" where a URL belongs. A note
    that reads "Filled in by AI from: source; source" is worse than one
    that admits it has nothing to show."""

    FOUND = {"listings": [{
        "title": "Frieze", "description": "An art fair.", "category": "exhibition",
        "price_tier": "premium", "location_name": "The park", "location_area": "London",
        "booking_url": "https://frieze.com/tickets", "start_date": "", "end_date": "",
        "mainstream_to_unusual": 3, "intimate_to_large_scale": 4, "tags": [],
    }]}

    def fill(self, sources):
        found = {"listings": [dict(self.FOUND["listings"][0], sources=sources)]}
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.research_listings", return_value=found):
            return self.client.post(reverse("desk:listings_add"),
                                    {"fill_from": "Frieze", "fill_area": "London"})

    def test_real_links_are_recorded(self):
        response = self.fill([{"title": "Frieze", "url": "https://frieze.com/london"}])
        self.assertIn("https://frieze.com/london",
                      response.context["form"].initial["editorial_note"])
        self.assertContains(response, "Filled in from 1 page")

    def test_a_placeholder_is_not_passed_off_as_a_link(self):
        response = self.fill([{"title": "Frieze", "url": "source"},
                              {"title": "Frieze", "url": "source"}])
        note = response.context["form"].initial["editorial_note"]
        self.assertNotIn("source; source", note)
        self.assertIn("did not name the pages", note)
        self.assertContains(response, "check every field")


class OverviewFiguresLeadSomewhereTests(DeskTestCase):
    def test_each_figure_is_a_link(self):
        page = self.client.get(reverse("desk:dashboard"))
        self.assertEqual(page.content.decode().count('<a class="d-stat'), 4)


class CatalogueWideSendsAreGoneTests(DeskTestCase):
    """Two buttons emailed people from the whole catalogue with nothing to
    look at first. One of them said Preview and showed a count."""

    def setUp(self):
        super().setUp()
        jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.reader = Reader.objects.create(email="ada@example.com", location="London")
        self.reader.interest_tags.add(jazz)
        for i in range(3):
            event(f"Gig {i}").tags.add(jazz)

    def test_neither_button_is_offered(self):
        page = self.client.get(reverse("desk:readers_list"))
        self.assertNotContains(page, "Preview, from everything")
        self.assertNotContains(page, "Send now, from everything")
        self.assertContains(page, "Suggest events &amp; send")

    def test_posting_the_old_send_action_sends_nothing(self):
        self.client.post(reverse("desk:readers_list"),
                         {"action": "send", "selected": [self.reader.pk]}, follow=True)
        self.assertEqual(NewsletterIssue.objects.count(), 0)

    def test_posting_the_old_preview_action_does_nothing(self):
        response = self.client.post(reverse("desk:readers_list"),
                                    {"action": "preview", "selected": [self.reader.pk]},
                                    follow=True)
        self.assertNotContains(response, "Dry run")
        self.assertEqual(NewsletterIssue.objects.count(), 0)


class TheInterestYouPickedNarrowsTheSendTests(DeskTestCase):
    """Picking comedy to choose who to write to, then being offered
    everything, is the mismatch that made results feel wrong."""

    def setUp(self):
        super().setUp()
        self.comedy = Tag.objects.create(name="Comedy", slug="comedy-nights")
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.reader = Reader.objects.create(email="ada@example.com", location="London")
        self.reader.interest_tags.add(self.comedy, self.jazz)
        self.standup = event("Stand-up night")
        self.standup.tags.add(self.comedy)
        self.gig = event("Late jazz")
        self.gig.tags.add(self.jazz)

    def test_the_choice_carries_the_interest_to_the_send_page(self):
        response = self.client.post(
            reverse("desk:readers_list") + "?tag=comedy-nights",
            {"action": "choose", "selected": [self.reader.pk]})
        self.assertIn("t=comedy-nights", response.url)

    def test_only_events_with_that_interest_are_offered(self):
        page = self.client.get(reverse("desk:send"),
                               {"r": self.reader.pk, "t": "comedy-nights"})
        titles = [row["event"].title for row in page.context["rows"]]
        self.assertEqual(titles, ["Stand-up night"])
        self.assertContains(page, "Only events tagged")

    def test_without_it_everything_that_suits_them_is_offered(self):
        page = self.client.get(reverse("desk:send"), {"r": self.reader.pk})
        titles = sorted(row["event"].title for row in page.context["rows"])
        self.assertEqual(titles, ["Late jazz", "Stand-up night"])
        self.assertNotContains(page, "Only events tagged")

    def test_the_narrowing_survives_a_preview(self):
        page = self.client.post(reverse("desk:send"), {
            "r": self.reader.pk, "t": "comedy-nights", "action": "preview",
            "event": [self.standup.pk]})
        self.assertEqual([row["event"].title for row in page.context["rows"]],
                         ["Stand-up night"])
        self.assertEqual(page.context["tags_raw"], "comedy-nights")


class OneNameForOneEngineTests(DeskTestCase):
    def test_research_is_called_the_same_thing_wherever_it_is_pressed(self):
        reader = Reader.objects.create(email="ada@example.com")
        for url in (reverse("desk:listings_list"),
                    reverse("desk:send") + f"?r={reader.pk}",
                    reverse("desk:interests_list")):
            page = self.client.get(url)
            self.assertContains(page, "Find events with AI")
            self.assertNotContains(page, "Find more with AI")
            self.assertNotContains(page, "Suggest events for my readers")


class OneExplanationForOneCauseTests(DeskTestCase):
    """The preview kept its own copy of the skip message, so the same
    situation was explained two different ways."""

    def test_the_preview_says_what_the_send_says(self):
        from recommendations.sending import preview_issue_for_reader

        reader = Reader.objects.create(email="ada@example.com")
        result = preview_issue_for_reader(reader, min_recommendations=99)
        self.assertFalse(result["ok"])
        self.assertIn("Nothing is published", result["message"])
        self.assertNotIn("Add more published listings", result["message"])


class ReviewWhatAIFoundTests(DeskTestCase):
    """AI research used to drop drafts into the catalogue and leave you to
    notice. Now everything it finds waits for a person to accept, reject,
    or correct first."""

    def setUp(self):
        super().setUp()
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.found = event("Trio at the Vortex", status=Opportunity.Status.DRAFT,
                           found_by_ai=True,
                           sources=[{"title": "The venue", "url": "https://vortex.example"}])
        self.found.tags.add(self.jazz)

    def test_the_queue_shows_what_is_waiting_with_its_sources(self):
        page = self.client.get(reverse("desk:listings_review"))
        self.assertContains(page, "Trio at the Vortex")
        self.assertContains(page, "https://vortex.example")
        self.assertContains(page, "Found by AI")

    def test_the_events_page_says_how_many_are_waiting(self):
        page = self.client.get(reverse("desk:listings_list"))
        self.assertContains(page, "waiting for you")
        self.assertContains(page, reverse("desk:listings_review"))

    def test_accepting_one_keeps_the_edits_and_puts_it_live(self):
        response = self.client.post(reverse("desk:listings_review"), {
            "accept_one": self.found.pk,
            f"{self.found.pk}-title": "Trio at the Vortex, corrected",
            f"{self.found.pk}-category": "music",
            f"{self.found.pk}-description": "A trio, downstairs.",
            f"{self.found.pk}-price_tier": "budget",
            f"{self.found.pk}-location_area": "Manchester",
            f"{self.found.pk}-booking_url": "https://vortex.example/book",
            f"{self.found.pk}-tags": [self.jazz.pk],
            f"{self.found.pk}-start_date": "", f"{self.found.pk}-end_date": "",
            f"{self.found.pk}-price_display": "", f"{self.found.pk}-location_name": "",
        }, follow=True)
        self.found.refresh_from_db()
        self.assertEqual(self.found.status, Opportunity.Status.PUBLISHED)
        self.assertEqual(self.found.title, "Trio at the Vortex, corrected")
        self.assertEqual(self.found.location_area, "Manchester")
        self.assertContains(response, "accepted and live")

    def test_accepting_a_batch_takes_them_as_they_are(self):
        second = event("Another one", status=Opportunity.Status.DRAFT)
        response = self.client.post(reverse("desk:listings_review"),
                                    {"action": "accept",
                                     "selected": [self.found.pk, second.pk]}, follow=True)
        for e in (self.found, second):
            e.refresh_from_db()
            self.assertEqual(e.status, Opportunity.Status.PUBLISHED)
        self.assertContains(response, "2 events accepted")

    def test_rejecting_deletes_it(self):
        self.client.post(reverse("desk:listings_review"),
                         {"action": "reject", "selected": [self.found.pk]}, follow=True)
        self.assertFalse(Opportunity.objects.filter(pk=self.found.pk).exists())

    def test_a_reject_cannot_touch_something_already_live(self):
        live = event("Already running")
        self.client.post(reverse("desk:listings_review"),
                         {"action": "reject", "selected": [live.pk]}, follow=True)
        self.assertTrue(Opportunity.objects.filter(pk=live.pk).exists())

    def test_nothing_waiting_says_so(self):
        self.found.delete()
        self.assertContains(self.client.get(reverse("desk:listings_review")),
                            "Nothing is waiting")

    def test_an_accepted_event_with_no_interests_is_flagged(self):
        bare = event("Untagged", status=Opportunity.Status.DRAFT)
        response = self.client.post(reverse("desk:listings_review"), {
            "accept_one": bare.pk,
            f"{bare.pk}-title": "Untagged", f"{bare.pk}-category": "music",
            f"{bare.pk}-description": "x", f"{bare.pk}-price_tier": "budget",
            f"{bare.pk}-location_area": "London",
            f"{bare.pk}-booking_url": "https://example.com/x",
            f"{bare.pk}-start_date": "", f"{bare.pk}-end_date": "",
            f"{bare.pk}-price_display": "", f"{bare.pk}-location_name": "",
        }, follow=True)
        self.assertContains(response, "can&#x27;t reach anyone yet")


class TheReviewQueueReadsWellTests(DeskTestCase):
    """Ten open cards with fifty interests down the side of each is a page
    nobody reviews."""

    def setUp(self):
        super().setUp()
        for i in range(3):
            event(f"Found {i}", status=Opportunity.Status.DRAFT, found_by_ai=True)
        # The seeded taxonomy already supplies the interests to pick from.

    def test_each_card_starts_collapsed_and_its_title_opens_it(self):
        page = self.client.get(reverse("desk:listings_review"))
        first = Opportunity.objects.filter(status=Opportunity.Status.DRAFT).first()
        self.assertContains(page, f'data-expand="card-{first.pk}"')
        self.assertContains(page, f'id="card-{first.pk}" hidden')

    def test_the_title_still_goes_somewhere_without_the_script(self):
        first = Opportunity.objects.filter(status=Opportunity.Status.DRAFT).first()
        self.assertContains(self.client.get(reverse("desk:listings_review")),
                            reverse("desk:listings_change", args=[first.pk]))

    def test_the_header_says_enough_to_judge_without_opening_it(self):
        page = self.client.get(reverse("desk:listings_review"))
        self.assertContains(page, "Found by AI")
        self.assertContains(page, "London")  # the area, from the fixture

    def test_interests_get_their_own_full_width_block_with_a_search_box(self):
        first = Opportunity.objects.filter(status=Opportunity.Status.DRAFT).first()
        page = self.client.get(reverse("desk:listings_review"))
        self.assertContains(page, "d-review-tags")
        self.assertContains(page, f'data-narrow="#tags-{first.pk} label"')
        # and not repeated inside the narrow column grid
        self.assertNotContains(page, f'id="id_{first.pk}-tags" class="d-check-grid"')

    def test_there_is_a_select_all(self):
        page = self.client.get(reverse("desk:listings_review"))
        self.assertContains(page, "data-select-all")
        self.assertContains(page, "Select all 3 on this page")

    def test_find_events_with_ai_is_not_on_this_page(self):
        page = self.client.get(reverse("desk:listings_review"))
        self.assertNotContains(page, 'value="suggest_for_readers"')

    def test_the_events_page_offers_a_button_not_a_sentence_with_a_link(self):
        page = self.client.get(reverse("desk:listings_list"))
        self.assertContains(page, 'class="d-btn small primary" href="%s"'
                            % reverse("desk:listings_review"))


class SavingAnEventRunsItTests(DeskTestCase):
    """There is no draft to forget about any more. What you save is live,
    and it is tagged as it is created so it can reach someone."""

    def test_the_form_has_no_status_field(self):
        page = self.client.get(reverse("desk:listings_add"))
        self.assertNotContains(page, 'name="status"')

    def test_a_saved_event_is_in_circulation(self):
        data = {"title": "Late set", "slug": "", "category": "music",
                "description": "x", "editorial_note": "", "price_tier": "budget",
                "price_display": "", "location_name": "", "location_area": "London",
                "booking_url": "https://example.com/late", "start_date": "", "end_date": "",
                "critic_rating": "", "critic_rating_source": "", "critic_quote": "",
                "mainstream_to_unusual": "3", "intimate_to_large_scale": "2"}
        with mock.patch("recommendations.ai.is_enabled", return_value=False):
            self.client.post(reverse("desk:listings_add"), data, follow=True)
        made = Opportunity.objects.get(title="Late set")
        self.assertEqual(made.status, Opportunity.Status.PUBLISHED)

    def test_editing_a_live_event_does_not_re_publish_an_archived_one(self):
        gone = event("Retired", status=Opportunity.Status.ARCHIVED)
        data = {"title": "Retired", "slug": gone.slug, "category": "music",
                "description": "x", "editorial_note": "", "price_tier": "budget",
                "price_display": "", "location_name": "", "location_area": "London",
                "booking_url": "https://example.com/r", "start_date": "", "end_date": "",
                "critic_rating": "", "critic_rating_source": "", "critic_quote": "",
                "mainstream_to_unusual": "3", "intimate_to_large_scale": "2"}
        self.client.post(reverse("desk:listings_change", args=[gone.pk]), data, follow=True)
        gone.refresh_from_db()
        self.assertEqual(gone.status, Opportunity.Status.ARCHIVED)


class OnlyArchiveSurvivesTests(DeskTestCase):
    """Publish, Back to draft and Tag with AI went. Archive stayed, because
    an ended event has to go somewhere and retiring keeps the feedback the
    matching learns from."""

    def test_the_bulk_bar_offers_archive_and_put_back_only(self):
        event("Something")
        page = self.client.get(reverse("desk:listings_list"))
        self.assertContains(page, 'value="archive"')
        self.assertContains(page, 'value="restore"')
        for gone in ('value="publish"', 'value="back_to_draft"', 'value="suggest"'):
            self.assertNotContains(page, gone)

    def test_archive_retires_and_put_back_returns(self):
        gig = event("Gig")
        self.client.post(reverse("desk:listings_list"),
                         {"action": "archive", "selected": [gig.pk]}, follow=True)
        gig.refresh_from_db()
        self.assertEqual(gig.status, Opportunity.Status.ARCHIVED)
        self.client.post(reverse("desk:listings_list"),
                         {"action": "restore", "selected": [gig.pk]}, follow=True)
        gig.refresh_from_db()
        self.assertEqual(gig.status, Opportunity.Status.PUBLISHED)

    def test_archiving_keeps_what_readers_said(self):
        gig = event("Gig")
        reader = Reader.objects.create(email="ada@example.com")
        issue = NewsletterIssue.objects.create(reader=reader)
        Recommendation.objects.create(issue=issue, opportunity=gig, rationale="x", score=1)
        self.client.post(reverse("desk:listings_list"),
                         {"action": "archive", "selected": [gig.pk]}, follow=True)
        self.assertEqual(Recommendation.objects.filter(opportunity=gig).count(), 1)

    def test_an_ended_event_still_archives_itself(self):
        gone = event("Over", end_date=timezone.localdate() - timedelta(days=1))
        Opportunity.archive_ended()
        gone.refresh_from_db()
        self.assertEqual(gone.status, Opportunity.Status.ARCHIVED)


class TheSuiteNeverSpendsMoneyTests(TestCase):
    """A real .env on the machine is how anyone checks the AI or the
    sending works. Without this, the suite quietly called OpenAI for every
    rationale it rendered and handed real messages to SendGrid."""

    def test_the_live_keys_are_neutralised_while_testing(self):
        from django.conf import settings

        self.assertEqual(settings.OPENAI_API_KEY, "")
        self.assertEqual(settings.SENDGRID_API_KEY, "")

    def test_so_ai_reports_itself_unavailable(self):
        from recommendations import ai

        self.assertFalse(ai.is_enabled())


class EndedEventsStillArchiveTests(DeskTestCase):
    """Nothing above should have touched this; it is the one behaviour the
    Events list changes on its own."""

    def test_an_ended_published_event_archives_itself(self):
        gone = event("Over", end_date=timezone.localdate() - timedelta(days=1))
        Opportunity.archive_ended()
        gone.refresh_from_db()
        self.assertEqual(gone.status, Opportunity.Status.ARCHIVED)
