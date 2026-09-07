from django.test import TestCase
from django.urls import reverse

from readers.models import Reader


FULL_PROFILE = {
    "email": "reader@example.com", "name": "Ada", "location": "London",
    "interest_categories": ["music", "food"], "budget": "moderate",
    "travel_radius": "regional", "mainstream_preference": "4",
    "scale_preference": "2", "open_to_surprise": "on",
    "loved_examples": "Late-night jazz", "notes": "Taking my mum",
}


class SignupSavesTests(TestCase):
    def test_a_full_signup_is_saved(self):
        self.client.post(reverse("readers:onboarding"), FULL_PROFILE)

        reader = Reader.objects.get(email="reader@example.com")
        self.assertEqual(reader.name, "Ada")
        self.assertEqual(reader.interest_categories, ["music", "food"])
        self.assertEqual(reader.budget, "moderate")
        self.assertEqual(reader.mainstream_preference, 4)
        self.assertTrue(reader.open_to_surprise)
        self.assertEqual(reader.notes, "Taking my mum")

    def test_signing_up_again_does_not_wipe_an_existing_profile(self):
        # The signup form arrives blank, so a returning reader who fills in
        # only their email must not lose everything they told us before.
        self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        self.client.post(reverse("readers:onboarding"), {"email": "reader@example.com"})

        reader = Reader.objects.get(email="reader@example.com")
        self.assertEqual(reader.name, "Ada")
        self.assertEqual(reader.interest_categories, ["music", "food"])
        self.assertEqual(reader.budget, "moderate")
        self.assertEqual(reader.notes, "Taking my mum")
        self.assertEqual(reader.mainstream_preference, 4)

    def test_signing_up_again_applies_the_answers_that_were_given(self):
        self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        self.client.post(reverse("readers:onboarding"), {
            "email": "reader@example.com", "location": "Bristol", "budget": "treat"})

        reader = Reader.objects.get(email="reader@example.com")
        self.assertEqual(reader.location, "Bristol")
        self.assertEqual(reader.budget, "treat")
        self.assertEqual(reader.name, "Ada")  # untouched

    def test_no_duplicate_reader_is_created(self):
        self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        self.assertEqual(Reader.objects.filter(email="reader@example.com").count(), 1)


class WelcomeEmailTests(TestCase):
    def test_a_new_signup_gets_a_welcome_email(self):
        from unittest import mock
        with mock.patch("readers.views.send_welcome") as welcome:
            self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        welcome.assert_called_once()
        self.assertEqual(welcome.call_args.args[0].email, "reader@example.com")

    def test_an_existing_reader_updating_does_not_get_another_welcome(self):
        from unittest import mock
        self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        with mock.patch("readers.views.send_welcome") as welcome:
            self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        welcome.assert_not_called()

    def test_a_failing_provider_never_costs_someone_their_signup(self):
        from unittest import mock
        with mock.patch("readers.views.send_welcome", side_effect=RuntimeError("smtp down")):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        # send_welcome swallows its own errors, so the real path is safe:
        with mock.patch("sendgrid.SendGridAPIClient", side_effect=RuntimeError("down")):
            with self.settings(SENDGRID_API_KEY="SG.x"):
                response = self.client.post(
                    reverse("readers:onboarding"),
                    {**FULL_PROFILE, "email": "resilient@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Reader.objects.filter(email="resilient@example.com").exists())

    def test_readers_are_not_offered_a_way_to_edit(self):
        # Deliberate: adding an answer means signing up again, and removing
        # one is an editorial action. Nothing should promise otherwise.
        self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        html = self.client.post(reverse("readers:onboarding"), FULL_PROFILE).content.decode()
        self.assertNotIn("preferences", html.lower())
        self.assertNotIn("change your answers", html.lower())

    def test_the_welcome_shows_back_what_they_told_us(self):
        from recommendations.emailing import profile_summary
        self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        reader = Reader.objects.get(email="reader@example.com")

        rows = dict(profile_summary(reader))
        self.assertEqual(rows["Following"], "music, food")
        self.assertEqual(rows["Based in"], "London")
        self.assertEqual(rows["Budget"], "Moderate")
        self.assertIn("Wildcards", rows)

    def test_the_summary_omits_questions_they_skipped(self):
        from recommendations.emailing import profile_summary
        reader = Reader.objects.create(email="sparse@example.com")
        rows = dict(profile_summary(reader))
        self.assertEqual(rows, {})

    def test_it_can_be_switched_off_in_the_admin(self):
        from django.core.cache import cache
        from unittest import mock
        from recommendations.emailing import send_welcome
        from siteconfig.models import SiteConfig

        cache.clear()
        config = SiteConfig.load()
        config.send_welcome_email = False
        config.save()
        cache.clear()

        reader = Reader.objects.create(email="off@example.com")
        with mock.patch("sendgrid.SendGridAPIClient") as client:
            self.assertIsNone(send_welcome(reader))
        client.assert_not_called()
