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


class LoadSampleEventsTests(DeskTestCase):
    """Its button is on the Events list, so the Events list has to answer
    it. It used to fall through to the bulk branch and say "Nothing
    selected", which made it a button that did nothing at all."""

    def test_the_button_on_the_events_page_actually_loads_them(self):
        before = Opportunity.objects.count()
        response = self.client.post(reverse("desk:listings_list"),
                                    {"load_sample_catalogue": "1"}, follow=True)
        self.assertGreater(Opportunity.objects.count(), before)
        self.assertContains(response, "sample events as drafts")

    def test_it_does_not_answer_with_nothing_selected(self):
        response = self.client.post(reverse("desk:listings_list"),
                                    {"load_sample_catalogue": "1"}, follow=True)
        self.assertNotContains(response, "Nothing selected")


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

    def test_the_classify_guard_points_at_settings_not_an_env_var(self):
        opp = event("Gig")
        with mock.patch("recommendations.ai.is_enabled", return_value=False):
            response = self.client.post(reverse("desk:listings_list"),
                                        {"action": "suggest", "selected": [opp.pk]},
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
