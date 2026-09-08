from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from siteconfig.emails import EmailTemplate, render_email
from siteconfig.models import SiteConfig


# The production static storage needs a manifest built by collectstatic,
# which tests don't run - swap it out for anything that renders admin pages.
plain_static = override_settings(STORAGES={
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})




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
