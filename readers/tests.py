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


class PreferencesPageTests(TestCase):
    def setUp(self):
        self.client.post(reverse("readers:onboarding"), FULL_PROFILE)
        self.reader = Reader.objects.get(email="reader@example.com")
        self.url = reverse("readers:preferences", args=[self.reader.edit_token])

    def test_the_page_arrives_filled_in_with_their_answers(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('value="reader@example.com"', html)
        self.assertIn('value="Ada"', html)
        self.assertIn("Late-night jazz", html)
        self.assertIn("Taking my mum", html)

    def test_editing_saves_and_confirms(self):
        data = {**FULL_PROFILE, "location": "Bristol", "interest_categories": ["film"]}
        html = self.client.post(self.url, data).content.decode()

        self.reader.refresh_from_db()
        self.assertEqual(self.reader.location, "Bristol")
        self.assertEqual(self.reader.interest_categories, ["film"])
        self.assertIn("Saved", html)

    def test_clearing_a_field_here_is_honoured(self):
        # The opposite of signup: they can see the value, so removing it is
        # deliberate and must stick.
        data = {**FULL_PROFILE, "notes": "", "loved_examples": ""}
        self.client.post(self.url, data)

        self.reader.refresh_from_db()
        self.assertEqual(self.reader.notes, "")
        self.assertEqual(self.reader.loved_examples, "")

    def test_an_unknown_token_is_a_404(self):
        import uuid
        self.assertEqual(
            self.client.get(
                reverse("readers:preferences", args=[uuid.uuid4()])).status_code, 404)

    def test_each_reader_gets_their_own_token(self):
        other = Reader.objects.create(email="other@example.com")
        self.assertNotEqual(other.edit_token, self.reader.edit_token)

    def test_the_thank_you_page_links_to_their_preferences(self):
        html = self.client.post(reverse("readers:onboarding"), FULL_PROFILE).content.decode()
        self.reader.refresh_from_db()
        self.assertIn(str(self.reader.edit_token), html)
