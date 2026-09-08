"""The desk: a from-scratch admin that owns every template and stylesheet
it uses, so nothing here can be undone by Django's contrib.admin assets.

Coverage mirrors what the desk replaces: every list and form page renders,
staff-only access is enforced, and each action (publish/archive, send/
preview a newsletter, campaign scheduling, template start-from-builtin)
does what it says.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

# The production static storage needs a manifest built by collectstatic,
# which tests don't run - swap it out for anything that renders desk pages.
plain_static = override_settings(STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})

from campaigns.models import Campaign, CampaignDelivery
from opportunities.models import Opportunity, Tag
from readers.models import Reader
from recommendations.models import NewsletterIssue, Recommendation
from siteconfig.emails import EmailTemplate
from siteconfig.models import SiteConfig


def staff_user(**kwargs):
    User = get_user_model()
    defaults = {"is_staff": True, "is_active": True}
    defaults.update(kwargs)
    user = User.objects.create_user("editor", "editor@example.com", "pw", **defaults)
    return user


def opportunity(**kwargs):
    defaults = {
        "title": "Trio residency", "category": "music", "description": "A jazz trio.",
        "price_tier": "budget", "location_area": "London", "booking_url": "https://example.com",
        "mainstream_to_unusual": 3, "intimate_to_large_scale": 2,
    }
    defaults.update(kwargs)
    return Opportunity.objects.create(**defaults)


@plain_static
class AccessTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("desk:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("desk:login"), response.url)

    def test_a_non_staff_user_is_refused_not_looped(self):
        User = get_user_model()
        User.objects.create_user("reader", "r@example.com", "pw", is_staff=False)
        self.client.login(username="reader", password="pw")
        response = self.client.get(reverse("desk:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_a_staff_user_gets_in(self):
        staff_user()
        self.client.login(username="editor", password="pw")
        response = self.client.get(reverse("desk:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_the_login_page_renders_signed_out(self):
        response = self.client.get(reverse("desk:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editorial desk")


@plain_static
class LoggedInTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = staff_user()
        self.client.login(username="editor", password="pw")


class PageRenderTests(LoggedInTestCase):
    """Every list and add page has to render with no data at all - the
    empty state is a real code path, not just a full one."""

    def test_every_list_and_add_page_renders_empty(self):
        urls = [
            reverse("desk:dashboard"), reverse("desk:listings_list"), reverse("desk:listings_add"),
            reverse("desk:interests_list"), reverse("desk:interests_add"),
            reverse("desk:readers_list"), reverse("desk:campaigns_list"), reverse("desk:campaigns_add"),
            reverse("desk:issues_list"), reverse("desk:recommendations_list"),
            reverse("desk:templates_list"), reverse("desk:templates_add"), reverse("desk:siteconfig"),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_change_pages_render_with_data(self):
        opp = opportunity()
        tag = Tag.objects.create(name="Jazz nights", category="music")
        reader = Reader.objects.create(email="ada@example.com", name="Ada")
        campaign = Campaign.objects.create(name="c", subject="s", body="b")
        template = EmailTemplate.objects.create(
            name="t", kind=EmailTemplate.Kind.NEWSLETTER, html_body="<p>{{ reader.name }}</p>")
        issue = NewsletterIssue.objects.create(reader=reader)
        Recommendation.objects.create(issue=issue, opportunity=opp, rationale="x")

        for url in [
            reverse("desk:listings_change", args=[opp.pk]),
            reverse("desk:interests_change", args=[tag.pk]),
            reverse("desk:readers_change", args=[reader.pk]),
            reverse("desk:campaigns_change", args=[campaign.pk]),
            reverse("desk:campaigns_preview", args=[campaign.pk]),
            reverse("desk:templates_change", args=[template.pk]),
            reverse("desk:templates_preview", args=[template.pk]),
            reverse("desk:issues_change", args=[issue.pk]),
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)


class ListingWorkflowTests(LoggedInTestCase):
    def test_add_a_listing(self):
        # A slug no seeded tag uses.
        tag = Tag.objects.create(name="Campaign test interest", slug="campaign-test-interest", category="music")
        response = self.client.post(reverse("desk:listings_add"), {
            "title": "A show", "category": "music", "status": "draft", "tags": [tag.pk],
            "description": "desc", "editorial_note": "", "price_tier": "budget",
            "price_display": "", "location_name": "", "location_area": "London",
            "is_online": "", "booking_url": "https://example.com",
            "start_date": "", "end_date": "", "critic_rating": "", "critic_rating_source": "",
            "critic_quote": "", "mainstream_to_unusual": 3, "intimate_to_large_scale": 2,
        })
        self.assertEqual(response.status_code, 302)
        obj = Opportunity.objects.get(title="A show")
        self.assertEqual(obj.created_by, self.user)
        self.assertIn(tag, obj.tags.all())

    def test_bulk_publish(self):
        opp = opportunity(status=Opportunity.Status.DRAFT)
        self.client.post(reverse("desk:listings_list"), {"action": "publish", "selected": [opp.pk]})
        opp.refresh_from_db()
        self.assertEqual(opp.status, Opportunity.Status.PUBLISHED)

    def test_search_and_filter(self):
        opportunity(title="Jazz basement", category="music", description="A basement bar.")
        opportunity(title="Pottery class", category="talk", description="Hand-building for beginners.")
        response = self.client.get(reverse("desk:listings_list"), {"q": "Jazz"})
        self.assertContains(response, "Jazz basement")
        self.assertNotContains(response, "Pottery class")
        response = self.client.get(reverse("desk:listings_list"), {"category": "talk"})
        self.assertContains(response, "Pottery class")
        self.assertNotContains(response, "Jazz basement")

    def test_load_sample_catalogue(self):
        before = Opportunity.objects.count()
        response = self.client.post(reverse("desk:listings_add"), {"load_sample_catalogue": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertGreater(Opportunity.objects.count(), before)


class ReaderWorkflowTests(LoggedInTestCase):
    @patch("desk.views.readers.send_issue_for_reader")
    def test_preview_and_send_actions(self, send):
        from recommendations.sending import SendResult

        send.return_value = SendResult(reader_email="x", sent=True, match_count=2, message="Sent 2.")
        reader = Reader.objects.create(email="ada@example.com")
        response = self.client.post(reverse("desk:readers_change", args=[reader.pk]),
                                    {"action": "send"})
        self.assertEqual(response.status_code, 302)
        send.assert_called_once_with(reader, dry_run=False)

    def test_filters_by_follows_and_status(self):
        Reader.objects.create(email="a@example.com", interest_categories=["music"])
        Reader.objects.create(email="b@example.com", interest_categories=[])
        response = self.client.get(reverse("desk:readers_list"), {"follows": "music"})
        self.assertContains(response, "a@example.com")
        self.assertNotContains(response, "b@example.com")


@override_settings(SENDGRID_API_KEY="test-key")
class CampaignWorkflowTests(LoggedInTestCase):
    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_schedule_then_send_now(self, deliver):
        Reader.objects.create(email="ada@example.com")
        campaign = Campaign.objects.create(name="c", subject="s", body="Hello.")
        self.client.post(reverse("desk:campaigns_schedule", args=[campaign.pk]))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.SCHEDULED)

        self.client.post(reverse("desk:campaigns_send_now", args=[campaign.pk]))
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.SENT)
        deliver.assert_called_once()

    @patch("campaigns.sending.deliver", return_value="msg-1")
    def test_a_test_send_goes_to_the_editor(self, deliver):
        Reader.objects.create(email="ada@example.com", name="Ada")
        campaign = Campaign.objects.create(name="c", subject="s", body="Hello {first_name}.")
        self.client.post(reverse("desk:campaigns_test_send", args=[campaign.pk]))
        self.assertEqual(deliver.call_args.args[0], "editor@example.com")
        self.assertFalse(campaign.deliveries.exists())

    def test_state_changes_need_a_post(self):
        campaign = Campaign.objects.create(name="c", subject="s", body="b")
        response = self.client.get(reverse("desk:campaigns_send_now", args=[campaign.pk]))
        self.assertEqual(response.status_code, 405)

    def test_retry_failed_deliveries(self):
        campaign = Campaign.objects.create(name="c", subject="s", body="b", status=Campaign.Status.SENT)
        reader = Reader.objects.create(email="a@example.com")
        CampaignDelivery.objects.create(campaign=campaign, reader=reader,
                                        status=CampaignDelivery.Status.FAILED, error="boom")
        self.client.post(reverse("desk:campaigns_list"), {"action": "retry_failed", "selected": [campaign.pk]})
        self.assertEqual(
            campaign.deliveries.get().status, CampaignDelivery.Status.PENDING)


class EmailTemplateWorkflowTests(LoggedInTestCase):
    def test_start_from_builtin_needs_a_post_and_creates_a_copy(self):
        url = reverse("desk:templates_from_builtin", args=["newsletter"])
        self.assertEqual(self.client.get(url).status_code, 405)
        before = EmailTemplate.objects.count()
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EmailTemplate.objects.count(), before + 1)

    def test_a_broken_template_is_rejected_on_save(self):
        response = self.client.post(reverse("desk:templates_add"), {
            "name": "bad", "kind": "newsletter", "subject": "", "notes": "",
            "html_body": "{% broken %}", "text_body": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "won&#x27;t render")
        self.assertFalse(EmailTemplate.objects.filter(name="bad").exists())


class SiteConfigTests(LoggedInTestCase):
    def test_saving_updates_the_singleton(self):
        config = SiteConfig.load()
        data = {f.name: getattr(config, f.name) for f in SiteConfig._meta.fields
                if f.name not in ("id", "updated_at") and not f.name.endswith("_id")}
        data = {k: ("" if v is None else v) for k, v in data.items()}
        data["site_name"] = "New Name"
        data["send_frequency"] = SiteConfig.Frequency.MANUAL
        data["send_weekday"] = 0
        data["send_day_of_month"] = 1
        data["send_hour"] = 8
        # Foreign keys serialize as their pk field name in a form post.
        for fk in ("welcome_template", "newsletter_template", "campaign_template"):
            data.pop(fk, None)
            data[fk] = ""
        response = self.client.post(reverse("desk:siteconfig"), data)
        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.status_code == 200 else None)
        self.assertEqual(SiteConfig.load().site_name, "New Name")

    def test_reset_wording(self):
        config = SiteConfig.load()
        config.site_name = "Drifted"
        config.save()
        self.client.post(reverse("desk:siteconfig_reset_wording"))
        self.assertEqual(SiteConfig.load().site_name, "The Ether")


class PreviewEscapingTests(LoggedInTestCase):
    """Ported from the old admin when its campaign preview was retired.

    The rendered email is a SafeString. Dropped into an attribute
    unescaped, its first double quote ends the srcdoc early and the
    iframe shows nothing.
    """

    def test_the_email_survives_the_srcdoc_attribute(self):
        Reader.objects.create(email="ada@example.com", name="Ada")
        campaign = Campaign.objects.create(name="c", subject="s", body="Hello.")
        response = self.client.get(reverse("desk:campaigns_preview", args=[campaign.pk]))
        self.assertContains(response, 'srcdoc="&lt;!doctype html&gt;')
        self.assertNotContains(response, 'srcdoc="<!doctype')


class TemplatePreviewTests(LoggedInTestCase):
    """Ported from the old admin: a preview renders from stand-in data, so
    it never depends on a real reader existing."""

    def test_preview_renders_without_touching_a_reader(self):
        self.assertFalse(Reader.objects.exists())
        template = EmailTemplate.objects.create(
            name="P", kind=EmailTemplate.Kind.NEWSLETTER,
            html_body="{% for rec in recommendations %}<b>{{ rec.opportunity.title }}</b>{% endfor %}")
        response = self.client.get(reverse("desk:templates_preview", args=[template.pk]))
        self.assertContains(response, "basement jazz room")
        self.assertFalse(Reader.objects.exists())


class WordingDriftTests(LoggedInTestCase):
    """Ported from the old admin: drift has to be visible on the page, not
    just computed - the reset button is useless if you can't see what it
    would change."""

    def test_drift_is_shown_on_the_settings_page(self):
        config = SiteConfig.load()
        config.tagline = "stale wording"
        config.save()
        cache.clear()
        response = self.client.get(reverse("desk:siteconfig"))
        self.assertContains(response, "stale wording")
        self.assertContains(response, "Take the shipped wording")


@plain_static
class RetiredAdminRedirectTests(TestCase):
    """The old admin's replaced screens send you to the desk, so a stale
    bookmark can't land on an unmaintained copy. Users and Groups stay
    where they are - the desk doesn't rebuild those."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_superuser("su", "su@example.com", "pw")
        self.client.login(username="su", password="pw")

    def test_replaced_screens_redirect_to_the_desk(self):
        for old, expected in [
            ("/admin/", reverse("desk:dashboard")),
            ("/admin/readers/reader/", reverse("desk:readers_list")),
            ("/admin/opportunities/opportunity/", reverse("desk:listings_list")),
            ("/admin/opportunities/tag/", reverse("desk:interests_list")),
            ("/admin/campaigns/campaign/", reverse("desk:campaigns_list")),
            ("/admin/recommendations/newsletterissue/", reverse("desk:issues_list")),
            ("/admin/recommendations/recommendation/", reverse("desk:recommendations_list")),
            ("/admin/siteconfig/siteconfig/", reverse("desk:siteconfig")),
            ("/admin/siteconfig/emailtemplate/", reverse("desk:templates_list")),
        ]:
            response = self.client.get(old)
            self.assertEqual(response.status_code, 302, old)
            self.assertEqual(response.url, expected, old)

    def test_a_deep_link_redirects_too(self):
        """Not just the list page - an old link to one record as well."""
        response = self.client.get("/admin/readers/reader/1/change/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("desk:readers_list"))

    def test_users_and_groups_now_redirect_too(self):
        """They were the last thing left in the old admin; the desk has its
        own now, so nothing there is reachable any more."""
        for old, expected in [("/admin/auth/user/", reverse("desk:users_list")),
                              ("/admin/auth/group/", reverse("desk:groups_list"))]:
            response = self.client.get(old)
            self.assertEqual(response.status_code, 302, old)
            self.assertEqual(response.url, expected, old)


class QueryCountTests(LoggedInTestCase):
    """The database is a continent away from the app, so every query is
    ~230ms on the page. A list making one query per row is the difference
    between a fast page and an eight-second one - these assert the count
    doesn't grow with the number of rows."""

    def counts_for(self, url):
        """Queries for a page with few rows vs many, so growth shows up."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        measured = []
        for _ in range(2):
            cache.clear()
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(url).status_code, 200)
            measured.append(len(ctx.captured_queries))
        return measured[-1]

    def test_the_campaigns_list_does_not_query_per_row(self):
        url = reverse("desk:campaigns_list")
        Reader.objects.create(email="a@example.com")
        for i in range(3):
            Campaign.objects.create(name=f"c{i}", subject="s", body="b")
        few = self.counts_for(url)

        for i in range(3, 18):
            Campaign.objects.create(name=f"c{i}", subject="s", body="b")
        many = self.counts_for(url)

        self.assertEqual(few, many,
                         f"{many - few} extra queries for 15 more campaigns - "
                         "something is querying per row again")

    def test_the_readers_list_does_not_query_per_row(self):
        url = reverse("desk:readers_list")
        for i in range(3):
            Reader.objects.create(email=f"r{i}@example.com")
        few = self.counts_for(url)

        for i in range(3, 18):
            Reader.objects.create(email=f"r{i}@example.com")
        self.assertEqual(few, self.counts_for(url))

    def test_the_listings_list_does_not_query_per_row(self):
        url = reverse("desk:listings_list")
        for i in range(3):
            opportunity(title=f"o{i}")
        few = self.counts_for(url)

        for i in range(3, 18):
            opportunity(title=f"o{i}")
        self.assertEqual(few, self.counts_for(url))


class DropdownWordingTests(LoggedInTestCase):
    """Django labels a dropdown's blank option "---------", which says
    nothing. It can't simply be deleted - on a required field it's the
    placeholder, and on an optional one it's the "none" value - so it's
    reworded instead, and these check it stays that way."""

    def test_no_page_renders_the_bare_dashes(self):
        opp = opportunity()
        tag = Tag.objects.create(name="Jazz nights", category="music")
        reader = Reader.objects.create(email="ada@example.com")
        campaign = Campaign.objects.create(name="c", subject="s", body="b")
        template = EmailTemplate.objects.create(
            name="t", kind=EmailTemplate.Kind.NEWSLETTER, html_body="<p>x</p>")

        for url in [
            reverse("desk:listings_add"), reverse("desk:listings_change", args=[opp.pk]),
            reverse("desk:interests_add"), reverse("desk:interests_change", args=[tag.pk]),
            reverse("desk:readers_change", args=[reader.pk]),
            reverse("desk:campaigns_add"), reverse("desk:campaigns_change", args=[campaign.pk]),
            reverse("desk:templates_add"), reverse("desk:templates_change", args=[template.pk]),
            reverse("desk:siteconfig"), reverse("desk:listings_list"),
        ]:
            response = self.client.get(url)
            self.assertNotContains(response, "---------", msg_prefix=url)

    def test_the_blank_option_is_still_there_and_says_what_it_means(self):
        from desk.forms import OpportunityForm, ReaderForm, SiteConfigForm

        # Required: a placeholder, so nothing is silently pre-selected.
        category = dict(OpportunityForm().fields["category"].choices)
        self.assertEqual(category[""], "Choose a category")

        # Optional: the blank is the "none" value and must remain selectable.
        budget = dict(ReaderForm().fields["budget"].choices)
        self.assertEqual(budget[""], "Not set")

        # Optional foreign key: blank means fall back to the built-in email.
        self.assertEqual(
            SiteConfigForm().fields["newsletter_template"].empty_label,
            "Use the built-in newsletter")

    def test_a_required_dropdown_still_rejects_an_empty_choice(self):
        """The wording change must not make the placeholder submittable."""
        response = self.client.post(reverse("desk:listings_add"), {
            "title": "No category", "category": "", "status": "draft",
            "description": "d", "price_tier": "budget", "location_area": "London",
            "booking_url": "https://example.com",
            "mainstream_to_unusual": 3, "intimate_to_large_scale": 2,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Opportunity.objects.filter(title="No category").exists())

    def test_an_optional_dropdown_can_still_be_cleared(self):
        reader = Reader.objects.create(email="ada@example.com", budget="moderate")
        data = {f: "" for f in (
            "name", "age", "location", "travel_radius", "other_travel",
            "travel_destinations", "mainstream_preference", "scale_preference",
            "other_categories", "other_interests", "loved_examples",
            "disliked_examples", "notes", "budget", "other_budget",
            "other_availability")}
        data["email"] = reader.email
        data["is_active"] = "on"
        response = self.client.post(reverse("desk:readers_change", args=[reader.pk]), data)
        self.assertEqual(response.status_code, 302)
        reader.refresh_from_db()
        self.assertEqual(reader.budget, "")


@plain_static
class AccessSectionTests(TestCase):
    """Users and Groups, the last thing that still pointed at Django's admin.

    Stricter than the rest of the desk on purpose: this is where someone
    could grant themselves more power than they were given.
    """

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.boss = User.objects.create_superuser("boss", "boss@example.com", "pw")
        self.editor = User.objects.create_user("ed", "ed@example.com", "pw", is_staff=True)

    def test_a_plain_editor_cannot_reach_or_even_see_accounts(self):
        self.client.login(username="ed", password="pw")
        for url in (reverse("desk:users_list"), reverse("desk:groups_list"),
                    reverse("desk:users_add"), reverse("desk:users_change", args=[self.boss.pk])):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        # And it isn't dangled in front of them either.
        self.assertNotContains(self.client.get(reverse("desk:dashboard")),
                               reverse("desk:users_list"))

    def test_a_superuser_gets_the_pages(self):
        self.client.login(username="boss", password="pw")
        for url in (reverse("desk:users_list"), reverse("desk:users_add"),
                    reverse("desk:users_change", args=[self.editor.pk]),
                    reverse("desk:users_password", args=[self.editor.pk]),
                    reverse("desk:groups_list"), reverse("desk:groups_add")):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(reverse("desk:dashboard")),
                            reverse("desk:users_list"))

    def test_adding_a_user_hashes_the_password(self):
        self.client.login(username="boss", password="pw")
        response = self.client.post(reverse("desk:users_add"), {
            "username": "newbie", "first_name": "", "last_name": "", "email": "n@example.com",
            "password1": "a-long-enough-passphrase", "password2": "a-long-enough-passphrase",
            "is_active": "on", "is_staff": "on",
        })
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="newbie")
        self.assertNotEqual(user.password, "a-long-enough-passphrase")
        self.assertTrue(user.check_password("a-long-enough-passphrase"))
        self.assertTrue(user.is_staff)

    def test_editing_a_user_never_touches_their_password(self):
        self.client.login(username="boss", password="pw")
        before = self.editor.password
        self.client.post(reverse("desk:users_change", args=[self.editor.pk]), {
            "username": "ed", "first_name": "Ed", "last_name": "", "email": "ed@example.com",
            "is_active": "on", "is_staff": "on",
        })
        self.editor.refresh_from_db()
        self.assertEqual(self.editor.first_name, "Ed")
        self.assertEqual(self.editor.password, before)

    def test_setting_a_password_applies_the_projects_validators(self):
        self.client.login(username="boss", password="pw")
        url = reverse("desk:users_password", args=[self.editor.pk])
        rejected = self.client.post(url, {"new_password1": "123", "new_password2": "123"})
        self.assertEqual(rejected.status_code, 200)
        self.editor.refresh_from_db()
        self.assertFalse(self.editor.check_password("123"))

        accepted = self.client.post(url, {"new_password1": "a-long-enough-passphrase",
                                          "new_password2": "a-long-enough-passphrase"})
        self.assertEqual(accepted.status_code, 302)
        self.editor.refresh_from_db()
        self.assertTrue(self.editor.check_password("a-long-enough-passphrase"))

    def test_changing_your_own_password_does_not_sign_you_out(self):
        self.client.login(username="boss", password="pw")
        self.client.post(reverse("desk:users_password", args=[self.boss.pk]),
                         {"new_password1": "another-long-passphrase",
                          "new_password2": "another-long-passphrase"})
        self.assertEqual(self.client.get(reverse("desk:users_list")).status_code, 200)

    def test_a_group_carries_only_this_projects_permissions(self):
        from desk.forms import GroupForm

        apps = {p.content_type.app_label for p in GroupForm().fields["permissions"].queryset}
        self.assertNotIn("admin", apps)
        self.assertNotIn("contenttypes", apps)
        self.assertTrue({"opportunities", "readers"} <= apps)


@plain_static
class SidebarAddLinkTests(LoggedInTestCase):
    """The "+" sits beside its row rather than below it, which it did when
    it was an <a> nested inside another <a> - invalid markup the parser
    repaired by closing the outer link early."""

    def test_the_add_link_is_a_sibling_not_nested_in_the_row_link(self):
        html = self.client.get(reverse("desk:dashboard")).content.decode()
        row = html.split('class="d-nav-row', 1)[1].split("</div>", 1)[0]
        self.assertIn('class="d-nav-link"', row)
        self.assertIn('class="d-add"', row)
        # The row link must be closed before the add link opens.
        self.assertLess(row.index("</a>"), row.index('class="d-add"'))

    def test_the_add_link_names_one_thing_not_many(self):
        html = self.client.get(reverse("desk:dashboard")).content.decode()
        self.assertIn('data-tip="Add listing"', html)
        self.assertIn('data-tip="Add interest"', html)
        self.assertNotIn('data-tip="Add listings"', html)
