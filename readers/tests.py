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


class PerInterestSettingsTests(TestCase):
    """A reader isn't the same about everything: they'll cross town for a gig
    and want the gallery round the corner."""

    def setUp(self):
        from opportunities.models import Tag

        self.jazz = Tag.objects.get_or_create(
            slug="jazz", defaults={"name": "jazz", "category": "music"})[0]
        self.art = Tag.objects.get_or_create(
            slug="contemporary-art", defaults={"name": "contemporary art",
                                               "category": "exhibition"})[0]

    def signup(self, **extra):
        data = {**FULL_PROFILE, "interest_tags": [self.jazz.pk, self.art.pk], **extra}
        return self.client.post(reverse("readers:onboarding"), data)

    def test_a_reader_can_set_travel_and_spend_for_one_interest(self):
        self.signup(**{
            f"pref_{self.jazz.pk}_travel_radius": "anywhere",
            f"pref_{self.jazz.pk}_budget": "treat",
            f"pref_{self.jazz.pk}_scale_preference": "1",
            f"pref_{self.art.pk}_travel_radius": "local_only",
        })
        reader = Reader.objects.get(email="reader@example.com")
        jazz = reader.interest_preferences.get(tag=self.jazz)
        self.assertEqual((jazz.travel_radius, jazz.budget, jazz.scale_preference),
                         ("anywhere", "treat", 1))
        self.assertEqual(reader.interest_preferences.get(tag=self.art).travel_radius, "local_only")
        # Their usual answers are untouched.
        self.assertEqual((reader.travel_radius, reader.budget), ("regional", "moderate"))

    def test_nothing_is_stored_for_an_interest_left_alone(self):
        self.signup()
        reader = Reader.objects.get(email="reader@example.com")
        self.assertEqual(reader.interest_preferences.count(), 0)

    def test_an_exception_for_an_interest_they_do_not_follow_is_dropped(self):
        self.signup(interest_tags=[self.jazz.pk],
                    **{f"pref_{self.art.pk}_budget": "no_limit"})
        reader = Reader.objects.get(email="reader@example.com")
        self.assertFalse(reader.interest_preferences.filter(tag=self.art).exists())

    def test_signing_up_again_does_not_wipe_an_exception(self):
        self.signup(**{f"pref_{self.jazz.pk}_travel_radius": "anywhere"})
        self.client.post(reverse("readers:onboarding"), {"email": "reader@example.com"})
        reader = Reader.objects.get(email="reader@example.com")
        self.assertEqual(reader.interest_preferences.get(tag=self.jazz).travel_radius, "anywhere")

    def test_a_whole_category_can_carry_the_settings_too(self):
        # Someone who picks "music" and never goes as far as "jazz" still
        # gets to say what they'd travel and spend on music.
        self.signup(interest_tags=[], **{"pref_cat_music_travel_radius": "anywhere",
                                         "pref_cat_music_budget": "treat"})
        reader = Reader.objects.get(email="reader@example.com")
        row = reader.interest_preferences.get(category="music")
        self.assertEqual((row.travel_radius, row.budget, row.tag_id), ("anywhere", "treat", None))
        self.assertEqual(row.label, "music (all of it)")

    def test_a_category_they_did_not_pick_is_dropped(self):
        self.signup(interest_tags=[], **{"pref_cat_theatre_budget": "no_limit"})
        reader = Reader.objects.get(email="reader@example.com")
        self.assertFalse(reader.interest_preferences.filter(category="theatre").exists())

    def test_when_theyre_free_can_differ_by_interest(self):
        self.signup(**{
            f"pref_{self.jazz.pk}_availability": ["weekday_evenings"],
            f"pref_{self.art.pk}_availability": ["weekends", "weekday_daytime"],
        })
        reader = Reader.objects.get(email="reader@example.com")
        self.assertEqual(reader.interest_preferences.get(tag=self.jazz).availability,
                         ["weekday_evenings"])
        self.assertEqual(sorted(reader.interest_preferences.get(tag=self.art).availability),
                         ["weekday_daytime", "weekends"])
        self.assertIn("free weekday evenings",
                      reader.interest_preferences.get(tag=self.jazz).summary())

    def ranked(self):
        reader = Reader.objects.get(email="reader@example.com")
        return [(p.label, p.rank, p.love) for p in
                reader.interest_preferences.select_related("tag").exclude(rank__isnull=True)]

    def test_the_biggest_bubble_comes_first(self):
        self.signup(interest_categories=["music", "exhibition", "theatre"], **{
            f"love_{self.art.pk}": "9", f"love_{self.jazz.pk}": "6", "love_cat_theatre": "3",
        })
        self.assertEqual(self.ranked(), [("contemporary art", 1, 9), ("jazz", 2, 6),
                                         ("theatre (all of it)", 3, 3)])
        # How much it matters isn't an exception: their usual answers still apply.
        reader = Reader.objects.get(email="reader@example.com")
        self.assertFalse(reader.interest_preferences.get(tag=self.jazz).is_set)

    def test_an_unsized_interest_takes_its_categorys_size(self):
        self.signup(interest_categories=["music", "exhibition"], **{
            "love_cat_music": "10", f"love_{self.art.pk}": "4"})
        # jazz was never sized itself, so it is as big as music.
        self.assertEqual(self.ranked(), [("jazz", 1, 10), ("contemporary art", 2, 4)])

    def test_leaving_every_bubble_alone_records_no_order(self):
        self.signup(interest_categories=["music", "exhibition"])
        self.assertEqual(self.ranked(), [])

    def test_the_signup_page_offers_the_settings(self):
        page = self.client.get(reverse("readers:onboarding")).content.decode()
        self.assertIn("Make it yours.", page)
        self.assertIn('id="universe"', page)
        self.assertNotIn("Put them in order.", page)
        self.assertIn(f'name="love_{self.jazz.pk}"', page)
        # Steps 4 and 5 are gone; their questions live in the Me bubble.
        self.assertNotIn("Where would you go, for something worth it?", page)
        self.assertNotIn("Budget &amp; timing.", page)
        for usual in ('name="travel_radius"', 'name="budget"', 'name="availability"'):
            self.assertIn(usual, page)
        self.assertIn(">Usual<", page)
        self.assertIn(f'name="pref_{self.jazz.pk}_travel_radius"', page)
        self.assertIn('name="pref_cat_music_travel_radius"', page)
        self.assertIn(f'name="pref_{self.jazz.pk}_availability"', page)



class SportTests(TestCase):
    """Sport is picked like anything else: the category, then the sports."""

    def test_step_two_offers_sport_and_step_three_its_sports(self):
        page = self.client.get(reverse("readers:onboarding")).content.decode()
        self.assertIn('name="interest_categories" value="sport"', page)
        from opportunities.models import Tag
        cricket = Tag.objects.get(slug="cricket")
        self.assertEqual(cricket.category, "sport")
        self.assertIn(f'value="{cricket.pk}"', page)
        self.assertIn('data-category="sport"', page)

    def test_a_reader_can_follow_sport_and_pick_their_sports(self):
        from opportunities.models import Tag
        cricket, tennis = Tag.objects.get(slug="cricket"), Tag.objects.get(slug="tennis")
        self.client.post(reverse("readers:onboarding"), {
            **FULL_PROFILE, "interest_categories": ["sport", "music"],
            "interest_tags": [cricket.pk, tennis.pk],
            f"love_{cricket.pk}": "10", f"love_{tennis.pk}": "7"})
        reader = Reader.objects.get(email="reader@example.com")
        self.assertIn("sport", reader.interest_categories)
        self.assertEqual([r["name"] for r in reader.ranked_interests()][:2], ["cricket", "tennis"])

    def test_the_old_sport_interest_moved_under_sport_and_kept_its_address(self):
        from opportunities.models import Tag
        old = Tag.objects.get(slug="sport")
        self.assertEqual((old.category, old.name), ("sport", "any live sport"))
