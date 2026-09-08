from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from siteconfig.emails import EmailTemplate, render_email
from siteconfig.models import SiteConfig


# The production static storage needs a manifest built by collectstatic,
# which tests don't run - swap it out for anything that renders admin pages.
plain_static = override_settings(STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})


def staff():
    User = get_user_model()
    user = User.objects.create_user("editor", password="x", is_staff=True, is_superuser=True)
    return user


@plain_static
class EmailTemplateTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_selected_template_is_used_instead_of_the_built_in_one(self):
        template = EmailTemplate.objects.create(
            name="Custom", kind=EmailTemplate.Kind.WELCOME,
            html_body="<p>Hello {{ reader.name }}</p>", text_body="Hello {{ reader.name }}")
        config = SiteConfig.load()
        config.welcome_template = template
        config.save()
        cache.clear()

        from types import SimpleNamespace
        html, text, _ = render_email(
            EmailTemplate.Kind.WELCOME,
            {"reader": SimpleNamespace(name="Ada"), "site_config": SiteConfig.load(),
             "summary": [], "unsubscribe_url": "u"},
            ("emails/welcome.html", "emails/welcome.txt"))

        self.assertIn("Hello Ada", html)
        self.assertIn("Hello Ada", text)

    def test_no_selection_falls_back_to_the_built_in_email(self):
        from types import SimpleNamespace
        cache.clear()
        html, _, subject = render_email(
            EmailTemplate.Kind.WELCOME,
            {"reader": SimpleNamespace(name="Ada", email="a@b.c"),
             "site_config": SiteConfig.load(), "summary": [], "unsubscribe_url": "u"},
            ("emails/welcome.html", "emails/welcome.txt"))
        self.assertIn("You're in", html)
        self.assertIsNone(subject)

    def test_a_template_that_breaks_at_send_time_falls_back(self):
        # Syntax is checked on save, but a runtime failure (a filter given the
        # wrong type, say) must not stop the email going out.
        template = EmailTemplate.objects.create(
            name="Breaks", kind=EmailTemplate.Kind.WELCOME,
            html_body="{{ reader.name|date:'x'|add:reader.missing.deeper }}")
        config = SiteConfig.load()
        config.welcome_template = template
        config.save()
        cache.clear()

        from types import SimpleNamespace
        html, _, _ = render_email(
            EmailTemplate.Kind.WELCOME,
            {"reader": SimpleNamespace(name="Ada", email="a@b.c"),
             "site_config": SiteConfig.load(), "summary": [], "unsubscribe_url": "u"},
            ("emails/welcome.html", "emails/welcome.txt"))
        self.assertIn("You're in", html)  # the built-in one

    def test_broken_syntax_is_rejected_before_it_can_be_saved(self):
        self.client.force_login(staff())
        response = self.client.post(
            reverse("admin:siteconfig_emailtemplate_add"),
            {"name": "Bad", "kind": "welcome", "subject": "",
             "html_body": "{% for x in %}", "text_body": "", "notes": ""})
        self.assertContains(response, "won&#x27;t render", status_code=200)
        self.assertFalse(EmailTemplate.objects.filter(name="Bad").exists())

    def test_starting_from_the_built_in_version_copies_it(self):
        self.client.force_login(staff())
        self.client.post(
            reverse("admin:siteconfig_emailtemplate_from_builtin", args=["newsletter"]),
            follow=True)
        template = EmailTemplate.objects.get(kind="newsletter")
        self.assertIn("{% for rec in recommendations %}", template.html_body)
        self.assertIsNone(template.check_syntax())

    def test_preview_renders_without_touching_a_reader(self):
        self.client.force_login(staff())
        template = EmailTemplate.objects.create(
            name="P", kind=EmailTemplate.Kind.NEWSLETTER,
            html_body="{% for rec in recommendations %}<b>{{ rec.opportunity.title }}</b>{% endfor %}")
        html = self.client.get(
            reverse("admin:siteconfig_emailtemplate_preview", args=[template.pk])
        ).content.decode()
        self.assertIn("basement jazz room", html)


class SendScheduleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.config = SiteConfig.load()

    def test_manual_never_sends_on_a_schedule(self):
        self.config.send_frequency = SiteConfig.Frequency.MANUAL
        should, why = self.config.is_send_day(date(2026, 9, 10))
        self.assertFalse(should)
        self.assertIn("manual", why.lower())

    def test_weekly_only_sends_on_the_chosen_day(self):
        self.config.send_frequency = SiteConfig.Frequency.WEEKLY
        self.config.send_weekday = 3  # Thursday
        self.assertTrue(self.config.is_send_day(date(2026, 9, 10))[0])   # a Thursday
        self.assertFalse(self.config.is_send_day(date(2026, 9, 11))[0])  # Friday

    def test_it_will_not_send_twice_in_one_day(self):
        self.config.send_frequency = SiteConfig.Frequency.WEEKLY
        self.config.send_weekday = 3
        self.config.last_sent_on = date(2026, 9, 10)
        should, why = self.config.is_send_day(date(2026, 9, 10))
        self.assertFalse(should)
        self.assertIn("Already sent", why)

    def test_fortnightly_skips_the_intervening_week(self):
        self.config.send_frequency = SiteConfig.Frequency.FORTNIGHTLY
        self.config.send_weekday = 3
        self.config.last_sent_on = date(2026, 9, 3)
        self.assertFalse(self.config.is_send_day(date(2026, 9, 10))[0])
        self.assertTrue(self.config.is_send_day(date(2026, 9, 17))[0])

    def test_monthly_sends_on_the_chosen_date(self):
        self.config.send_frequency = SiteConfig.Frequency.MONTHLY
        self.config.send_day_of_month = 15
        self.assertTrue(self.config.is_send_day(date(2026, 9, 15))[0])
        self.assertFalse(self.config.is_send_day(date(2026, 9, 16))[0])


@plain_static
class TabbedAdminTests(TestCase):
    def setUp(self):
        # SiteConfig.load() caches, and test transactions roll back without
        # invalidating it - so a config cached by another test would point at
        # a row that no longer exists here.
        cache.clear()

    def test_the_settings_page_renders_tabs(self):
        self.client.force_login(staff())
        response = self.client.get(
            reverse("admin:siteconfig_siteconfig_change", args=[SiteConfig.load().pk]))
        self.assertEqual(response.status_code, 200, f"got {response.status_code} "
                         f"-> {response.get('Location', '')}")
        html = response.content.decode()
        self.assertIn("cd-tabs", html)
        for section in ["Branding", "Sending", "Schedule", "Email templates",
                        "Learning from feedback", "AI assistance"]:
            self.assertIn(section, html)


@plain_static
class ShippedWordingTests(TestCase):
    """Defaults only apply when a row is created, so an improved default in
    code never reaches an existing site. That has already caught us twice -
    the tagline and the subject line both had to be corrected by hand."""

    def setUp(self):
        cache.clear()
        self.config = SiteConfig.load()

    def test_a_fresh_configuration_reports_no_drift(self):
        self.config.reset_to_defaults()
        self.assertEqual(self.config.drift_from_defaults(), [])

    def test_customised_wording_is_reported_as_drift(self):
        self.config.tagline = "something an editor wrote"
        self.config.save()

        drift = {d["field"]: d for d in self.config.drift_from_defaults()}

        self.assertIn("tagline", drift)
        self.assertEqual(drift["tagline"]["current"], "something an editor wrote")
        self.assertIn("filtered", drift["tagline"]["shipped"])

    def test_stale_wording_from_an_older_default_is_reported(self):
        # Exactly the case that bit us: the row holds last release's text.
        self.config.subject_template = "{name}{count} things you'll probably love this week"
        self.config.save()

        fields = [d["field"] for d in self.config.drift_from_defaults()]

        self.assertIn("subject_template", fields)

    def test_resetting_restores_the_shipped_wording(self):
        self.config.tagline = "stale"
        self.config.hero_headline = "also stale"
        self.config.save()

        changed = self.config.reset_to_defaults()

        self.assertEqual(set(changed), {"tagline", "hero_headline"})
        self.config.refresh_from_db()
        self.assertEqual(self.config.drift_from_defaults(), [])

    def test_resetting_leaves_settings_that_are_not_wording_alone(self):
        self.config.tagline = "stale"
        self.config.recommendations_per_send = 9
        self.config.weight_tag_overlap = 7.5
        self.config.save()

        self.config.reset_to_defaults()

        self.config.refresh_from_db()
        self.assertEqual(self.config.recommendations_per_send, 9)
        self.assertEqual(self.config.weight_tag_overlap, 7.5)

    def test_the_admin_offers_the_reset_and_it_works(self):
        self.config.tagline = "stale wording"
        self.config.save()
        cache.clear()
        self.client.force_login(staff())

        page = self.client.get(reverse(
            "admin:siteconfig_siteconfig_change", args=[self.config.pk])).content.decode()
        self.assertIn("stale wording", page)
        self.assertIn("Take the shipped wording", page)

        self.client.get(reverse("admin:siteconfig_reset_wording"), follow=True)

        self.config.refresh_from_db()
        self.assertIn("filtered", self.config.tagline)



@plain_static
class EmailTemplateButtonsTests(TestCase):
    """The starting points and the preview are reachable from the pages."""

    def setUp(self):
        cache.clear()
        self.client.force_login(get_user_model().objects.create_superuser(
            "tb", "tb@example.com", "pw"))

    def test_list_page_offers_the_built_in_starting_points(self):
        response = self.client.get(reverse("admin:siteconfig_emailtemplate_changelist"))
        for kind in ("welcome", "newsletter", "campaign"):
            self.assertContains(
                response, reverse("admin:siteconfig_emailtemplate_from_builtin", args=[kind]))

    def test_change_page_offers_preview(self):
        template = EmailTemplate.objects.create(
            name="t", kind=EmailTemplate.Kind.CAMPAIGN, html_body="<p>{{ body_html }}</p>")
        response = self.client.get(
            reverse("admin:siteconfig_emailtemplate_change", args=[template.pk]))
        self.assertContains(
            response, reverse("admin:siteconfig_emailtemplate_preview", args=[template.pk]))

    def test_starting_from_built_in_creates_a_copy_and_needs_a_post(self):
        url = reverse("admin:siteconfig_emailtemplate_from_builtin", args=["campaign"])
        self.assertEqual(self.client.get(url).status_code, 405)
        before = EmailTemplate.objects.count()
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EmailTemplate.objects.count(), before + 1)
        self.assertIn("{{ body_html }}", EmailTemplate.objects.latest("pk").html_body)
