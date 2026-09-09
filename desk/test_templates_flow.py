"""Templates, campaigns from an idea, and the five-entry sidebar.

What a template does on its own is a set of switches, and every switch
has a test for the two things that matter: it does the thing when on,
and it does nothing irreversible before the editor has seen the result.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from campaigns import templates_runner
from campaigns.models import Campaign, CampaignDelivery, SavedTemplate
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
        for name in ("Listings", "Users", "Templates", "Campaigns", "Insights"):
            self.assertIn(f">{name}</a>", nav)
        for gone in ("Interests", "Send by interest", "Newsletter issues",
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

    def test_interests_live_on_the_listings_page(self):
        page = self.client.get(reverse("desk:listings_list"))
        self.assertContains(page, reverse("desk:interests_add"))
        self.assertEqual(self.client.get(reverse("desk:interests_list")).status_code, 302)

    def test_the_health_panel_moved_to_settings(self):
        overview = self.client.get(reverse("desk:dashboard"))
        self.assertNotContains(overview, "Secret key")
        settings_page = self.client.get(reverse("desk:siteconfig"))
        self.assertContains(settings_page, "Secret key")

    def test_email_designs_moved_path_but_kept_their_names(self):
        self.assertEqual(reverse("desk:templates_list"), "/desk/email-designs/")
        self.assertEqual(reverse("desk:saved_templates_list"), "/desk/templates/")
        self.assertEqual(self.client.get(reverse("desk:templates_list")).status_code, 200)


class SavedTemplateAudienceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.ada = Reader.objects.create(email="ada@example.com", location="London")
        self.ada.interest_tags.add(self.jazz)
        self.bo = Reader.objects.create(email="bo@example.com", location="Leeds")
        self.cy = Reader.objects.create(email="cy@example.com", location="London",
                                        is_active=False)

    def test_no_rules_and_no_picks_means_everyone_active(self):
        t = SavedTemplate.objects.create(name="all")
        self.assertEqual(set(t.audience()), {self.ada, self.bo})

    def test_hand_picked_readers_alone_means_just_them(self):
        t = SavedTemplate.objects.create(name="picked")
        t.readers.add(self.bo)
        self.assertEqual(list(t.audience()), [self.bo])

    def test_picks_and_rules_combine(self):
        t = SavedTemplate.objects.create(name="both")
        t.readers.add(self.bo)
        t.audience_tags.add(self.jazz)
        self.assertEqual(set(t.audience()), {self.ada, self.bo})

    def test_an_inactive_reader_is_never_included_even_if_picked(self):
        t = SavedTemplate.objects.create(name="picked")
        t.readers.add(self.cy)
        self.assertEqual(list(t.audience()), [])

    def test_location_narrows(self):
        t = SavedTemplate.objects.create(name="london", audience_location="London")
        self.assertEqual(list(t.audience()), [self.ada])

    def test_the_description_says_what_was_set(self):
        t = SavedTemplate.objects.create(name="x", audience_location="London")
        t.readers.add(self.bo)
        t.audience_tags.add(self.jazz)
        d = t.audience_description()
        self.assertIn("1 reader you picked", d)
        self.assertIn("Jazz nights", d)
        self.assertIn("London", d)


class SavedTemplateScheduleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.today = timezone.localdate()

    def make(self, **kw):
        data = dict(name="weekly", frequency=Campaign.Frequency.WEEKLY,
                    status=SavedTemplate.Status.ACTIVE,
                    starts_on=self.today - timedelta(days=14), send_hour=0)
        data.update(kw)
        return SavedTemplate.objects.create(**data)

    def test_once_only_runs_by_hand(self):
        due, why = self.make(frequency=Campaign.Frequency.ONCE).due_for_a_run()
        self.assertFalse(due)
        self.assertIn("press Run", why)

    def test_a_draft_or_paused_template_never_runs(self):
        for status in (SavedTemplate.Status.DRAFT, SavedTemplate.Status.PAUSED):
            self.assertFalse(self.make(status=status).due_for_a_run()[0])

    def test_active_weekly_is_due_after_a_week(self):
        self.assertTrue(self.make().due_for_a_run()[0])
        self.assertFalse(self.make(last_run_on=self.today - timedelta(days=2)).due_for_a_run()[0])

    def test_daily_runs_every_day_but_not_twice(self):
        t = self.make(frequency=Campaign.Frequency.DAILY,
                      last_run_on=self.today - timedelta(days=1))
        self.assertTrue(t.due_for_a_run()[0])
        t.last_run_on = self.today
        self.assertFalse(t.due_for_a_run()[0])

    def test_it_respects_the_end_date(self):
        self.assertFalse(self.make(ends_on=self.today - timedelta(days=1)).due_for_a_run()[0])


class TemplateRunnerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.ada = Reader.objects.create(email="ada@example.com", location="London",
                                         interest_categories=["music"])
        self.ada.interest_tags.add(self.jazz)
        for i in range(3):
            listing(f"Gig {i}").tags.add(self.jazz)

    def test_a_newsletter_template_sends_each_reader_their_own_picks(self):
        t = SavedTemplate.objects.create(name="jazz", kind=SavedTemplate.Kind.NEWSLETTER)
        t.audience_tags.add(self.jazz)
        report = templates_runner.run(t)
        self.assertTrue(report["ok"])
        self.assertEqual(report["sent"], 1)
        self.assertEqual(NewsletterIssue.objects.get().reader, self.ada)
        t.refresh_from_db()
        self.assertEqual(t.runs, 1)
        self.assertEqual(t.last_run_on, timezone.localdate())

    def test_a_campaign_template_becomes_a_real_campaign_with_deliveries(self):
        t = SavedTemplate.objects.create(
            name="bulletin", kind=SavedTemplate.Kind.CAMPAIGN,
            subject="This week", brief="Things on.", auto_write=False)
        t.audience_tags.add(self.jazz)
        report = templates_runner.run(t)
        self.assertTrue(report["ok"])
        campaign = report["campaign"]
        self.assertIsInstance(campaign, Campaign)
        self.assertEqual(list(campaign.audience_tags.all()), [self.jazz])
        self.assertEqual(CampaignDelivery.objects.filter(campaign=campaign).count(), 1)

    def test_an_empty_campaign_template_refuses_rather_than_sending_nothing(self):
        t = SavedTemplate.objects.create(name="empty", kind=SavedTemplate.Kind.CAMPAIGN)
        report = templates_runner.run(t)
        self.assertFalse(report["ok"])
        self.assertEqual(Campaign.objects.count(), 0)

    def test_nobody_in_the_audience_is_reported_not_swallowed(self):
        t = SavedTemplate.objects.create(name="none", audience_location="Nowhere")
        report = templates_runner.run(t)
        self.assertFalse(report["ok"])
        self.assertIn("Nobody", report["message"])

    def test_auto_select_lets_ai_reset_the_audience_before_a_run(self):
        t = SavedTemplate.objects.create(name="auto", auto_select_audience=True,
                                         kind=SavedTemplate.Kind.NEWSLETTER)
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.suggest_audience", return_value={
                 "tags": ["jazz-nights", "made-up"], "categories": ["music", "nope"],
                 "location": "", "reasoning": ""}):
            report = templates_runner.run(t)
        t.refresh_from_db()
        self.assertEqual(list(t.audience_tags.all()), [self.jazz])
        self.assertEqual(t.audience_categories, ["music"])
        self.assertIn("AI set the audience", report["message"])

    def test_the_scheduler_runs_what_is_due_and_leaves_the_rest(self):
        due = SavedTemplate.objects.create(
            name="due", status=SavedTemplate.Status.ACTIVE,
            frequency=Campaign.Frequency.DAILY,
            starts_on=timezone.localdate() - timedelta(days=1), send_hour=0)
        SavedTemplate.objects.create(name="draft", frequency=Campaign.Frequency.DAILY,
                                     starts_on=timezone.localdate(), send_hour=0)
        lines = templates_runner.run_due(budget_seconds=30)
        self.assertEqual(len(lines), 1)
        self.assertIn("due", lines[0])
        due.refresh_from_db()
        self.assertEqual(due.runs, 1)


class TemplateScreenTests(DeskCase):
    def setUp(self):
        super().setUp()
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.ada = Reader.objects.create(email="ada@example.com", location="London",
                                         interest_categories=["music"])
        self.ada.interest_tags.add(self.jazz)
        for i in range(3):
            listing(f"Gig {i}").tags.add(self.jazz)

    def _form(self, **over):
        data = {"name": "Jazz weekly", "kind": "newsletter", "status": "draft",
                "subject": "", "brief": "", "body": "", "auto_write": "on", "auto_tag": "on",
                "link_label": "Book / learn more", "link_url": "",
                "audience_location": "", "frequency": "once", "starts_on": "",
                "ends_on": "", "send_hour": "9", "audience_tags": [self.jazz.pk]}
        data.update(over)
        return data

    def test_saving_confirms_and_shows_the_audience(self):
        response = self.client.post(reverse("desk:saved_templates_add"), self._form(),
                                    follow=True)
        self.assertContains(response, "Saved “Jazz weekly”.")
        self.assertContains(response, "<strong>1</strong> reader", html=False)
        self.assertEqual(SavedTemplate.objects.get().created_by, self.boss)

    def test_a_repeating_template_needs_a_start_date(self):
        response = self.client.post(reverse("desk:saved_templates_add"),
                                    self._form(frequency="weekly"))
        self.assertContains(response, "needs a start date")
        self.assertEqual(SavedTemplate.objects.count(), 0)

    def test_preview_renders_the_real_email_and_stores_nothing(self):
        t = SavedTemplate.objects.create(name="jazz")
        t.audience_tags.add(self.jazz)
        page = self.client.get(reverse("desk:saved_templates_preview", args=[t.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertIsNone(page.context["error"])
        self.assertIn("Gig", page.context["preview"]["html"])
        self.assertEqual(NewsletterIssue.objects.count(), 0)

    def test_run_now_really_sends_and_says_so(self):
        t = SavedTemplate.objects.create(name="jazz")
        t.audience_tags.add(self.jazz)
        response = self.client.post(reverse("desk:saved_templates_run", args=[t.pk]),
                                    follow=True)
        self.assertContains(response, "Newsletter sent to 1")
        self.assertEqual(NewsletterIssue.objects.count(), 1)

    def test_auto_tag_asks_ai_on_save_and_resolves_against_our_tags(self):
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("recommendations.ai.suggest_audience", return_value={
                 "tags": ["jazz-nights", "invented"], "categories": [],
                 "location": "", "reasoning": ""}):
            response = self.client.post(
                reverse("desk:saved_templates_add"),
                self._form(kind="campaign", brief="A jazz thing.", audience_tags=[]),
                follow=True)
        t = SavedTemplate.objects.get()
        self.assertEqual(list(t.audience_tags.all()), [self.jazz])
        self.assertContains(response, "AI suggested interests: Jazz nights")

    def test_a_campaign_template_can_start_a_campaign(self):
        t = SavedTemplate.objects.create(name="bulletin", kind=SavedTemplate.Kind.CAMPAIGN,
                                         subject="s", brief="b")
        t.audience_tags.add(self.jazz)
        response = self.client.post(reverse("desk:saved_templates_to_campaign", args=[t.pk]),
                                    follow=True)
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)
        self.assertEqual(list(campaign.audience_tags.all()), [self.jazz])
        self.assertContains(response, "Started")
        self.assertEqual(CampaignDelivery.objects.count(), 0)  # nothing sent

    def test_a_newsletter_template_cannot_become_a_campaign(self):
        t = SavedTemplate.objects.create(name="jazz")
        response = self.client.post(reverse("desk:saved_templates_to_campaign", args=[t.pk]),
                                    follow=True)
        self.assertContains(response, "Only a campaign template")
        self.assertEqual(Campaign.objects.count(), 0)

    def test_delete_goes_through_the_confirmation(self):
        t = SavedTemplate.objects.create(name="jazz")
        page = self.client.get(reverse("desk:delete", args=["saved_templates", t.pk]))
        self.assertContains(page, "Paused")
        self.assertTrue(SavedTemplate.objects.filter(pk=t.pk).exists())


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

    def test_a_template_starts_a_campaign_from_the_list(self):
        t = SavedTemplate.objects.create(name="bulletin", kind=SavedTemplate.Kind.CAMPAIGN,
                                         subject="s", brief="b")
        page = self.client.get(reverse("desk:campaigns_list"))
        self.assertContains(page, reverse("desk:campaigns_from_template", args=[t.pk]))
        self.client.post(reverse("desk:campaigns_from_template", args=[t.pk]))
        self.assertEqual(Campaign.objects.count(), 1)
