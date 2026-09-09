"""The admin guide, executed.

Every step the guide tells an editor to take, and every "you'll know it
worked because…" it promises, run against the real views. A guide that
claims a verification the software doesn't actually give you is worse
than no guide, so each check here is the reader's check, not a proxy for
it: the message they are told to look for, the pill they are told to see,
the number they are told will move.

If one of these fails, the guide is wrong - fix the guide (or the
software), not the assertion.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from campaigns.models import Campaign, CampaignDelivery
from config import dashboard
from opportunities.models import Opportunity, Tag
from readers.models import Reader
from recommendations.models import NewsletterIssue, Recommendation
from siteconfig.emails import EmailTemplate
from siteconfig.models import SiteConfig

from .tests import plain_static


def messages_in(response):
    return [m.message for m in response.context["messages"]] if response.context else []


def follow(client, url, data=None):
    """POST and land on the page the editor lands on, messages included."""
    return client.post(url, data or {}, follow=True)


@plain_static
class GuideWalkthrough(TestCase):
    """One editor, one sitting, in the order the guide puts it."""

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.boss = User.objects.create_superuser("boss", "boss@example.com", "pw")
        self.client.login(username="boss", password="pw")

    # -- Step 1: signing in ---------------------------------------------

    def test_01_signing_in_lands_on_the_overview(self):
        self.client.logout()
        response = self.client.post(reverse("desk:login"),
                                    {"username": "boss", "password": "pw"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editorial desk")
        self.assertContains(response, "boss")  # named in the sidebar footer

    def test_02_an_account_without_editor_access_is_told_so(self):
        get_user_model().objects.create_user("nobody", "n@example.com", "pw", is_staff=False)
        self.client.logout()
        self.client.login(username="nobody", password="pw")
        self.assertEqual(self.client.get(reverse("desk:dashboard")).status_code, 403)

    # -- Step 2: an interest ---------------------------------------------

    def test_03_adding_an_interest_confirms_by_name_and_lists_it(self):
        response = follow(self.client, reverse("desk:interests_add"),
                          {"name": "Late-night jazz", "slug": "", "category": "music"})
        self.assertIn("Saved “Late-night jazz”.", messages_in(response))
        self.assertContains(self.client.get(reverse("desk:listings_list")), "Late-night jazz")
        # The guide says leaving the slug blank fills it in for you.
        self.assertEqual(Tag.objects.get(name="Late-night jazz").slug, "late-night-jazz")

    def test_04_an_unused_interest_is_flagged_on_its_row(self):
        Tag.objects.create(name="Late-night jazz", slug="late-night-jazz", category="music")
        self.assertContains(self.client.get(reverse("desk:listings_list")), "nothing to send yet")

    # -- Step 3: a listing ------------------------------------------------

    def _add_listing(self, **overrides):
        data = {
            "title": "Trio residency", "slug": "", "category": "music", "status": "draft",
            "description": "A jazz trio in a basement.", "editorial_note": "",
            "price_tier": "budget", "price_display": "", "location_name": "",
            "location_area": "London", "booking_url": "https://example.com/book",
            "start_date": "", "end_date": "", "critic_rating": "",
            "critic_rating_source": "", "critic_quote": "",
            "mainstream_to_unusual": "3", "intimate_to_large_scale": "2",
        }
        data.update(overrides)
        return follow(self.client, reverse("desk:listings_add"), data)

    def test_05_a_new_listing_saves_as_a_draft_and_says_so(self):
        response = self._add_listing()
        self.assertIn("Saved “Trio residency”.", messages_in(response))
        listing = Opportunity.objects.get(title="Trio residency")
        self.assertEqual(listing.status, Opportunity.Status.DRAFT)
        # The pill reads DRAFT on screen, but that is CSS uppercasing "Draft" -
        # so the guide says "a grey Draft pill", which is what is really there.
        self.assertContains(self.client.get(reverse("desk:listings_list")),
                            '<span class="d-pill neutral">Draft</span>', html=True)

    def test_06_a_draft_is_never_recommended_to_anyone(self):
        """The guide's claim that a draft is invisible to readers."""
        from recommendations import matching

        listing = Opportunity.objects.create(
            title="Trio residency", category="music", description="x", price_tier="budget",
            location_area="London", booking_url="https://example.com",
            mainstream_to_unusual=3, intimate_to_large_scale=2,
            status=Opportunity.Status.DRAFT)
        reader = Reader.objects.create(email="ada@example.com", location="London",
                                       interest_categories=["music"])
        self.assertEqual(matching.top_matches_for_reader(reader), [])

        listing.status = Opportunity.Status.PUBLISHED
        listing.save(update_fields=["status"])
        self.assertTrue(matching.top_matches_for_reader(reader))

    def test_07_publishing_from_the_list_reports_how_many_and_shows_live(self):
        self._add_listing()
        listing = Opportunity.objects.get(title="Trio residency")
        response = follow(self.client, reverse("desk:listings_list"),
                          {"action": "publish", "selected": [listing.pk]})
        self.assertIn("1 listing marked published.", messages_in(response))
        self.assertContains(response, "Live")

    def test_08_the_overview_count_moves_as_soon_as_you_publish(self):
        """The check the guide gives you has to work on the next refresh.

        These figures used to be cached for a minute, so publishing and
        looking at the number showed the old one. Caching them again -
        even with invalidation - would break this: bulk Publish goes
        through queryset.update(), which fires no signals.
        """
        self._add_listing()
        listing = Opportunity.objects.get(title="Trio residency")
        before = self.client.get(reverse("desk:dashboard")).context["stats"]
        follow(self.client, reverse("desk:listings_list"),
               {"action": "publish", "selected": [listing.pk]})
        after = self.client.get(reverse("desk:dashboard")).context["stats"]
        self.assertEqual(after["opportunities"]["live"],
                         before["opportunities"]["live"] + 1)
        self.assertEqual(after["opportunities"]["draft"],
                         before["opportunities"]["draft"] - 1)

    def test_08b_the_overview_costs_a_fixed_six_queries(self):
        """Uncached, so its cost has to stay small and stay flat."""
        for i in range(5):
            self._published_listing(f"Gig {i}")
        Reader.objects.create(email="ada@example.com")
        with self.assertNumQueries(6):
            dashboard.stats()

    def test_08c_the_merged_counts_still_agree_with_counting_separately(self):
        """tags and issues are one aggregate each now, not two queries."""
        tagged = Tag.objects.create(name="Basement sets", slug="basement-sets")
        Tag.objects.create(name="Nothing uses this", slug="nothing-uses-this")
        listing = self._published_listing("Gig")
        listing.tags.add(tagged)
        reader = Reader.objects.create(email="ada@example.com")
        NewsletterIssue.objects.create(reader=reader, sent_at=timezone.now())
        NewsletterIssue.objects.create(reader=reader)

        data = dashboard.stats()
        self.assertEqual(data["tags_total"], Tag.objects.count())
        self.assertEqual(data["tags_unused"],
                         Tag.objects.filter(opportunities__isnull=True).count())
        self.assertEqual(data["issues_total"], NewsletterIssue.objects.count())
        self.assertEqual(data["issues_unsent"],
                         NewsletterIssue.objects.filter(sent_at__isnull=True).count())

    # -- Step 4: readers ---------------------------------------------------

    def _published_listing(self, title, **kw):
        data = dict(title=title, category="music", description="A jazz trio.",
                    price_tier="budget", location_area="London",
                    booking_url="https://example.com", mainstream_to_unusual=3,
                    intimate_to_large_scale=2, status=Opportunity.Status.PUBLISHED)
        data.update(kw)
        return Opportunity.objects.create(**data)

    def test_09_preview_newsletter_is_a_dry_run_that_leaves_no_trace(self):
        for i in range(3):
            self._published_listing(f"Gig {i}")
        reader = Reader.objects.create(email="ada@example.com", location="London",
                                       interest_categories=["music"])
        response = follow(self.client, reverse("desk:readers_list"),
                          {"action": "preview", "selected": [reader.pk]})
        said = " ".join(messages_in(response))
        self.assertIn("Dry run: would send", said)
        # Nothing recorded, nothing sent, nothing put on cooldown.
        self.assertEqual(NewsletterIssue.objects.count(), 0)
        self.assertEqual(Recommendation.objects.count(), 0)

    def test_10_too_few_matches_says_skipped_and_why(self):
        self._published_listing("Only one")
        reader = Reader.objects.create(email="ada@example.com", location="London",
                                       interest_categories=["music"])
        response = follow(self.client, reverse("desk:readers_list"),
                          {"action": "preview", "selected": [reader.pk]})
        said = " ".join(messages_in(response))
        self.assertIn("Skipped: only 1 strong match", said)
        self.assertIn("needs 2", said)

    def test_11_sending_for_real_records_an_issue_you_can_open(self):
        for i in range(3):
            self._published_listing(f"Gig {i}")
        reader = Reader.objects.create(email="ada@example.com", location="London",
                                       interest_categories=["music"])
        response = follow(self.client, reverse("desk:readers_list"),
                          {"action": "send", "selected": [reader.pk]})
        self.assertIn("Sent", " ".join(messages_in(response)))
        issue = NewsletterIssue.objects.get()
        self.assertIsNotNone(issue.sent_at)
        # The guide says it then appears under the reader, and in Issues.
        self.assertContains(self.client.get(
            reverse("desk:readers_change", args=[reader.pk])), f"Issue #{issue.pk}")
        self.assertContains(self.client.get(reverse("desk:issues_list")), "ada@example.com")

    def test_12_unticking_is_active_stops_the_scheduled_send(self):
        """The guide's advice to deactivate rather than delete."""
        from recommendations.sending import send_scheduled_newsletter

        for i in range(3):
            self._published_listing(f"Gig {i}")
        Reader.objects.create(email="ada@example.com", location="London",
                              interest_categories=["music"], is_active=False)
        config = SiteConfig.load()
        config.send_frequency = SiteConfig.Frequency.WEEKLY
        config.send_weekday = timezone.localdate().weekday()
        config.send_hour = 0
        config.save()
        report = send_scheduled_newsletter(budget_seconds=5)
        self.assertTrue(report["ran"])
        self.assertEqual(report["sent"], 0)
        self.assertEqual(NewsletterIssue.objects.count(), 0)

    # -- Step 5: site configuration ---------------------------------------

    def _save_config(self, **overrides):
        config = SiteConfig.load()
        data = {}
        for field in config._meta.fields:
            if field.name in ("id", "updated_at"):
                continue
            value = getattr(config, field.name)
            if value is None or value == "":
                continue
            data[field.name] = value.pk if hasattr(value, "pk") else value
        data.update(overrides)
        return follow(self.client, reverse("desk:siteconfig"), data)

    def test_13_saving_the_configuration_confirms_and_takes_effect_at_once(self):
        response = self._save_config(recommendations_per_send=2, min_recommendations=1)
        self.assertIn("Saved.", messages_in(response))
        # No cache lag here: saving clears it, so the new value is live.
        self.assertEqual(SiteConfig.load().recommendations_per_send, 2)

    def test_14_recommendations_per_send_changes_what_a_preview_builds(self):
        for i in range(6):
            self._published_listing(f"Gig {i}")
        reader = Reader.objects.create(email="ada@example.com", location="London",
                                       interest_categories=["music"])
        self._save_config(recommendations_per_send=2, min_recommendations=1)
        response = follow(self.client, reverse("desk:readers_list"),
                          {"action": "preview", "selected": [reader.pk]})
        self.assertIn("ada@example.com: Dry run: would send 2 recommendations.",
                      messages_in(response))

        self._save_config(recommendations_per_send=5, min_recommendations=1)
        response = follow(self.client, reverse("desk:readers_list"),
                          {"action": "preview", "selected": [reader.pk]})
        self.assertIn("ada@example.com: Dry run: would send 5 recommendations.",
                      messages_in(response))

    def test_15_cooldown_stops_the_same_listing_coming_round_again(self):
        from recommendations import matching

        listing = self._published_listing("Gig")
        reader = Reader.objects.create(email="ada@example.com", location="London",
                                       interest_categories=["music"])
        self.assertTrue(matching.top_matches_for_reader(reader))

        issue = NewsletterIssue.objects.create(reader=reader, sent_at=timezone.now())
        Recommendation.objects.create(issue=issue, opportunity=listing, rationale="x")
        self.assertEqual(matching.top_matches_for_reader(reader), [],
                         "inside the cooldown it should not be offered again")

        # Age the recommendation past the cooldown and it returns.
        Recommendation.objects.update(
            created_at=timezone.now() - timedelta(days=SiteConfig.load().cooldown_days + 1))
        self.assertTrue(matching.top_matches_for_reader(reader))

    def test_16_the_schedule_tab_decides_whether_today_is_a_send_day(self):
        config = SiteConfig.load()
        self.assertEqual(config.send_frequency, SiteConfig.Frequency.MANUAL)
        self.assertFalse(config.should_send_now()[0])
        self.assertIn("manual", config.should_send_now()[1].lower())

        self._save_config(send_frequency="weekly",
                          send_weekday=timezone.localdate().weekday(), send_hour=0)
        self.assertTrue(SiteConfig.load().should_send_now()[0])

        self._save_config(send_frequency="weekly",
                          send_weekday=(timezone.localdate().weekday() + 1) % 7, send_hour=0)
        ok, why = SiteConfig.load().should_send_now()
        self.assertFalse(ok)
        self.assertIn("Not the send day", why)

    # -- Step 6: campaigns --------------------------------------------------

    def _add_campaign(self, **overrides):
        # The schedule fields carry model defaults, so the real form arrives
        # with these pre-filled; the test has to send what the browser would.
        data = {"name": "Frieze weekend", "subject": "This weekend",
                "brief": "Frieze is on. Free entry on Sunday.", "body": "",
                "personalise": "on", "link_label": "", "link_url": "",
                "audience_location": "", "send_at": "",
                "frequency": "once", "starts_on": "", "ends_on": "", "send_hour": "9"}
        data.update(overrides)
        return follow(self.client, reverse("desk:campaigns_add"), data)

    def test_17_a_new_campaign_is_a_draft_that_sends_nothing(self):
        response = self._add_campaign()
        self.assertIn("Saved “Frieze weekend”.", messages_in(response))
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)
        self.assertEqual(CampaignDelivery.objects.count(), 0)
        self.assertContains(response, "Nothing goes out until you press Schedule or Send now.")

    def test_18_the_audience_count_reflects_the_restrictions_you_set(self):
        Reader.objects.create(email="london@example.com", location="London", is_active=True)
        Reader.objects.create(email="leeds@example.com", location="Leeds", is_active=True)
        self._add_campaign()
        campaign = Campaign.objects.get()

        page = self.client.get(reverse("desk:campaigns_change", args=[campaign.pk]))
        self.assertEqual(page.context["audience_count"], 2)

        self._add_campaign(name="Frieze weekend", audience_location="London")
        narrowed = Campaign.objects.get(audience_location="London")
        page = self.client.get(reverse("desk:campaigns_change", args=[narrowed.pk]))
        self.assertEqual(page.context["audience_count"], 1)

    def test_19_preview_renders_without_sending(self):
        Reader.objects.create(email="ada@example.com", location="London")
        self._add_campaign()
        campaign = Campaign.objects.get()
        response = self.client.get(reverse("desk:campaigns_preview", args=[campaign.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["error"])
        self.assertEqual(CampaignDelivery.objects.count(), 0)

    def test_20_scheduling_says_when_and_cancelling_stops_it(self):
        Reader.objects.create(email="ada@example.com", location="London")
        self._add_campaign()
        campaign = Campaign.objects.get()

        response = follow(self.client, reverse("desk:campaigns_schedule", args=[campaign.pk]))
        self.assertIn("scheduled", " ".join(messages_in(response)).lower())
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.SCHEDULED)

        response = follow(self.client, reverse("desk:campaigns_cancel", args=[campaign.pk]))
        self.assertIn("Anyone not yet emailed won't be.", " ".join(messages_in(response)))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.CANCELLED)

    def test_21_send_now_records_one_delivery_row_per_reader(self):
        Reader.objects.create(email="ada@example.com", location="London")
        Reader.objects.create(email="bo@example.com", location="London")
        self._add_campaign()
        campaign = Campaign.objects.get()
        follow(self.client, reverse("desk:campaigns_send_now", args=[campaign.pk]))
        campaign.refresh_from_db()
        self.assertEqual(campaign.deliveries.count(), 2)
        page = self.client.get(reverse("desk:campaigns_change", args=[campaign.pk]))
        self.assertContains(page, "ada@example.com")

    def test_22_a_test_send_needs_an_address_on_your_own_account(self):
        self.boss.email = ""
        self.boss.save(update_fields=["email"])
        self._add_campaign()
        campaign = Campaign.objects.get()
        response = follow(self.client, reverse("desk:campaigns_test_send", args=[campaign.pk]))
        self.assertIn("Your account has no email address", " ".join(messages_in(response)))

    # -- Step 7: email templates -------------------------------------------

    def test_23_start_from_builtin_gives_you_an_editable_copy(self):
        response = follow(self.client,
                          reverse("desk:templates_from_builtin", args=["newsletter"]))
        self.assertIn("Copied the built-in version.", " ".join(messages_in(response)))
        template = EmailTemplate.objects.get()
        self.assertTrue(template.html_body.strip())
        self.assertContains(self.client.get(
            reverse("desk:templates_preview", args=[template.pk])), "Subject:")

    def test_24_a_template_that_would_not_render_is_refused_on_save(self):
        response = self.client.post(reverse("desk:templates_add"), {
            "name": "Broken", "kind": "newsletter", "subject": "x",
            "html_body": "{% for x in %}", "text_body": "", "notes": ""})
        self.assertEqual(response.status_code, 200)  # redisplayed, not saved
        self.assertContains(response, "won&#x27;t render")
        self.assertEqual(EmailTemplate.objects.count(), 0)

    def test_25_a_template_is_only_live_once_selected_in_configuration(self):
        follow(self.client, reverse("desk:templates_from_builtin", args=["newsletter"]))
        template = EmailTemplate.objects.get()

        page = self.client.get(reverse("desk:templates_list"))
        self.assertContains(page, "No")  # the In use column

        self._save_config(newsletter_template=template.pk)
        self.assertEqual(SiteConfig.load().newsletter_template_id, template.pk)
        page = self.client.get(reverse("desk:templates_list"))
        self.assertContains(page, "Yes")

    # -- Step 8: accounts ----------------------------------------------------

    def test_26_only_a_superuser_reaches_the_accounts_pages(self):
        get_user_model().objects.create_user("ed", "ed@example.com", "pw", is_staff=True)
        self.client.logout()
        self.client.login(username="ed", password="pw")
        self.assertEqual(self.client.get(reverse("desk:users_list")).status_code, 403)
        # and it isn't dangled in front of them in the sidebar
        self.assertNotContains(self.client.get(reverse("desk:dashboard")), "/desk/users/")

    def test_27_a_new_editor_can_sign_in_and_a_non_editor_cannot(self):
        follow(self.client, reverse("desk:users_add"), {
            "username": "newed", "first_name": "", "last_name": "", "email": "n@example.com",
            "is_active": "on", "is_staff": "on", "password1": "correct-horse-9",
            "password2": "correct-horse-9"})
        self.client.logout()
        self.assertTrue(self.client.login(username="newed", password="correct-horse-9"))
        self.assertEqual(self.client.get(reverse("desk:dashboard")).status_code, 200)

        user = get_user_model().objects.get(username="newed")
        user.is_staff = False
        user.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get(reverse("desk:dashboard")).status_code, 403)

    def test_28_you_cannot_delete_the_account_you_are_signed_in_with(self):
        response = follow(self.client, reverse("desk:delete", args=["users", self.boss.pk]))
        self.assertIn("You can't delete the account you're signed in with.",
                      messages_in(response))
        self.assertTrue(get_user_model().objects.filter(pk=self.boss.pk).exists())

    # -- Step 9: deleting ----------------------------------------------------

    def test_29_the_delete_page_names_what_else_would_go(self):
        listing = self._published_listing("Gig")
        reader = Reader.objects.create(email="ada@example.com")
        issue = NewsletterIssue.objects.create(reader=reader)
        Recommendation.objects.create(issue=issue, opportunity=listing, rationale="x")

        page = self.client.get(reverse("desk:delete", args=["listings", listing.pk]))
        self.assertContains(page, "This also deletes")
        self.assertContains(page, "1 recommendation")
        self.assertContains(page, "Archiving keeps")
        # Looking is safe.
        self.assertTrue(Opportunity.objects.filter(pk=listing.pk).exists())

    def test_30_archiving_keeps_the_history_that_deleting_burns(self):
        listing = self._published_listing("Gig")
        reader = Reader.objects.create(email="ada@example.com")
        issue = NewsletterIssue.objects.create(reader=reader)
        Recommendation.objects.create(issue=issue, opportunity=listing, rationale="x")

        follow(self.client, reverse("desk:listings_list"),
               {"action": "archive", "selected": [listing.pk]})
        self.assertEqual(Recommendation.objects.count(), 1)

        follow(self.client, reverse("desk:delete", args=["listings", listing.pk]))
        self.assertEqual(Recommendation.objects.count(), 0)

    # -- Step 10: what runs on its own ---------------------------------------

    @override_settings(SCHEDULER_TOKEN="")
    def test_31_without_a_token_the_scheduler_endpoint_refuses(self):
        self.assertEqual(self.client.get(reverse("run-scheduled")).status_code, 503)

    @override_settings(SCHEDULER_TOKEN="s3cret")
    def test_32_the_scheduler_endpoint_checks_the_token(self):
        from unittest.mock import patch

        self.assertEqual(self.client.get(reverse("run-scheduled"),
                                         {"token": "wrong"}).status_code, 403)
        # The real call starts a background pass; here only the gate matters.
        with patch("campaigns.views.start_background_run", return_value=True):
            self.assertIn(self.client.get(reverse("run-scheduled"),
                                          {"token": "s3cret"}).status_code, (200, 202))

    def test_33_the_overview_reports_the_scheduler_and_the_database(self):
        names = {row["name"] for row in dashboard.configuration()}
        self.assertTrue({"Database", "Email sending", "AI assistance", "Scheduler",
                         "Debug mode", "Secret key"} <= names)
        # The health rows moved off the Overview to Settings → Health; the
        # Overview only says whether anything there needs attention.
        page = self.client.get(reverse("desk:siteconfig"))
        self.assertContains(page, "Scheduler")
        self.assertContains(page, "Health")
        overview = self.client.get(reverse("desk:dashboard"))
        self.assertNotContains(overview, "Secret key")
