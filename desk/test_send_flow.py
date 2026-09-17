"""The send: tick users, see what suits them, pick, read one, send."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from opportunities.models import Opportunity, Tag
from readers.models import Reader
from recommendations.models import NewsletterIssue

from .tests import plain_static


def event(title, tags=(), **kw):
    data = dict(title=title, category="music", description="x", price_tier="budget",
                location_area="London", booking_url=f"https://example.com/{title}",
                mainstream_to_unusual=3, intimate_to_large_scale=2,
                status=Opportunity.Status.PUBLISHED)
    data.update(kw)
    e = Opportunity.objects.create(**data)
    e.tags.add(*tags)
    return e


@plain_static
class SendFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.login(username="boss", password="pw")
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.food = Tag.objects.create(name="Midnight suppers", slug="midnight-suppers")
        self.ada = Reader.objects.create(email="ada@example.com", location="London",
                                         interest_categories=["music"])
        self.ada.interest_tags.add(self.jazz)
        self.bo = Reader.objects.create(email="bo@example.com", location="London",
                                        interest_categories=["food"])
        self.bo.interest_tags.add(self.food)
        self.gigs = [event(f"Gig {i}", tags=[self.jazz]) for i in range(3)]
        self.supper = event("Supper", tags=[self.food], category="food")
        self.url = reverse("desk:send") + f"?r={self.ada.pk},{self.bo.pk}"

    def test_users_arrive_from_the_users_page_ticked(self):
        response = self.client.post(reverse("desk:readers_list"),
                                    {"action": "choose", "selected": [self.ada.pk, self.bo.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("desk:send"), response.url)
        self.assertIn(str(self.ada.pk), response.url)

    def test_no_users_sends_you_back(self):
        response = self.client.get(reverse("desk:send"))
        self.assertRedirects(response, reverse("desk:readers_list"))

    def test_suggestions_are_the_events_the_matching_would_pick_with_reach(self):
        page = self.client.get(self.url)
        rows = {r["event"].title: r for r in page.context["rows"]}
        self.assertIn("Supper", rows)
        # Supper is offered because it suits Bo. It may sit low in Ada's
        # list too - the matching keeps breadth - but it is not her first pick.
        self.assertIn("bo@example.com", [r.email for r in rows["Supper"]["suits"]])
        from recommendations import matching
        self.assertNotEqual(matching.top_matches_for_reader(self.ada, limit=1)[0].opportunity,
                            self.supper)
        for gig in self.gigs:
            self.assertIn(gig.title, rows)
        # the widest-reaching events come first, pre-ticked
        self.assertTrue(page.context["rows"][0]["preticked"])

    def test_arriving_from_an_event_starts_with_that_event_ticked(self):
        """Coming from "good for 2 users" on the Events page, the event you
        came from is the one already ticked."""
        page = self.client.get(self.url + f"&e={self.supper.pk}")
        self.assertIn(self.supper.pk, page.context["chosen"])

    def test_an_event_the_matching_will_not_offer_says_so_rather_than_vanishing(self):
        draft = event("Not published yet", tags=[self.jazz],
                      status=Opportunity.Status.DRAFT)
        page = self.client.get(self.url + f"&e={draft.pk}", follow=True)
        self.assertNotIn(draft.pk, page.context["chosen"])
        self.assertContains(page, "Not published yet")
        self.assertContains(page, "isn&#x27;t in this list")

    def test_preview_builds_one_users_email_from_the_ticked_events_only(self):
        response = self.client.post(reverse("desk:send"), {
            "r": f"{self.ada.pk},{self.bo.pk}", "action": "preview",
            "preview_reader": self.ada.pk,
            "event": [self.gigs[0].pk, self.gigs[1].pk]}, follow=True)
        preview = response.context["preview"]
        self.assertTrue(preview["ok"], preview.get("message"))
        titles = {p["title"] for p in preview["picks"]}
        self.assertEqual(titles, {"Gig 0", "Gig 1"})
        self.assertEqual(NewsletterIssue.objects.count(), 0)

    def test_send_writes_each_user_their_own_email_from_the_pool(self):
        # Two picks each, from a pool of four, so the choice has to differ.
        from siteconfig.models import SiteConfig
        config = SiteConfig.load()
        config.recommendations_per_send = 2
        config.min_recommendations = 1
        config.save()
        response = self.client.post(reverse("desk:send"), {
            "r": f"{self.ada.pk},{self.bo.pk}", "action": "send",
            "event": [g.pk for g in self.gigs] + [self.supper.pk]}, follow=True)
        self.assertContains(response, "Sent to 2 users")
        ada_issue = NewsletterIssue.objects.get(reader=self.ada)
        bo_issue = NewsletterIssue.objects.get(reader=self.bo)
        ada_titles = set(ada_issue.recommendations.values_list("opportunity__title", flat=True))
        bo_titles = set(bo_issue.recommendations.values_list("opportunity__title", flat=True))
        self.assertNotEqual(ada_titles, bo_titles)   # their own picks, not one broadcast
        self.assertIn("Supper", bo_titles)
        self.assertNotIn("Supper", ada_titles)

    def test_a_user_the_pool_does_not_suit_is_skipped_not_padded(self):
        from siteconfig.models import SiteConfig
        config = SiteConfig.load(); config.min_recommendations = 2; config.save()
        response = self.client.post(reverse("desk:send"), {
            "r": f"{self.ada.pk},{self.bo.pk}", "action": "send",
            "event": [self.supper.pk]}, follow=True)
        said = " ".join(m.message for m in response.context["messages"])
        self.assertIn("ada@example.com: Skipped", said)
        self.assertFalse(NewsletterIssue.objects.filter(reader=self.ada).exists())

    def test_nothing_ticked_sends_nothing(self):
        self.client.post(reverse("desk:send"), {"r": f"{self.ada.pk}", "action": "send"}, follow=True)
        self.assertEqual(NewsletterIssue.objects.count(), 0)

    def test_find_more_with_ai_researches_for_these_users(self):
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("opportunities.research.for_readers", return_value=["Midnight suppers"]) as fr:
            response = self.client.post(reverse("desk:send"),
                                        {"r": f"{self.bo.pk}", "action": "research"}, follow=True)
        self.assertEqual(list(fr.call_args.args[0]), [self.bo])
        self.assertContains(response, "Searching for events for Midnight suppers")

    def test_the_pool_never_overrides_who_it_suits(self):
        """Ticking every event still gives each user their own ranking."""
        from recommendations import matching
        picks = matching.top_matches_for_reader(self.bo, pool=[g.pk for g in self.gigs] + [self.supper.pk])
        self.assertEqual(picks[0].opportunity, self.supper)


@plain_static
class UserRowPanelTests(TestCase):
    """Click a user's name and see what they like, how far, how much."""

    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.login(username="boss", password="pw")
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.rooms = Tag.objects.create(name="Basement rooms", slug="basement-rooms")
        self.arena = Tag.objects.create(name="Arena shows", slug="arena-shows")
        self.ada = Reader.objects.create(
            email="ada@example.com", name="Ada", location="London",
            travel_radius=Reader.TravelRadius.WITHIN_CITY, budget=Reader.Budget.MODERATE,
            other_budget="more for something special", other_travel="Brighton at a push",
            availability=["weekends", "weekday_evenings"])
        self.ada.interest_tags.add(self.jazz)
        self.ada.ai_inferred_tags.add(self.rooms)
        self.ada.ai_avoid_tags.add(self.arena)

    def test_the_panel_carries_interests_travel_and_pay(self):
        page = self.client.get(reverse("desk:readers_list"))
        self.assertContains(page, f'data-expand="user-{self.ada.pk}"')
        self.assertContains(page, f'id="user-{self.ada.pk}"')
        for text in ("Jazz nights", "Basement rooms", "Arena shows",
                     "Anywhere in my city", "Brighton at a push",
                     "more for something special", "Weekends, Weekday evenings"):
            self.assertContains(page, text)

    def test_the_panel_carries_what_ai_read_into_their_words(self):
        self.ada.ai_taste_summary = "Likes small rooms and a short set; avoids arenas."
        self.ada.save(update_fields=["ai_taste_summary"])
        page = self.client.get(reverse("desk:readers_list"))
        self.assertContains(page, "Likes small rooms and a short set")

    def test_the_three_columns_the_panel_replaces_are_gone(self):
        page = self.client.get(reverse("desk:readers_list")).content.decode()
        head = page.split("<thead>", 1)[1].split("</thead>", 1)[0]
        for gone in ("Follows", "Budget", "Travel"):
            self.assertNotIn(f"<th>{gone}</th>", head)

    def test_the_name_still_links_to_the_page_without_the_script(self):
        page = self.client.get(reverse("desk:readers_list"))
        self.assertContains(page, f'href="{reverse("desk:readers_change", args=[self.ada.pk])}" data-expand')

    def test_the_interest_picker_has_a_search_box(self):
        page = self.client.get(reverse("desk:readers_list"))
        self.assertContains(page, 'data-narrow="#interest-chips .d-chip-check"')


@plain_static
class EditThePreviewTests(TestCase):
    """The editor reads a user's culture week, changes it, and sends exactly that."""

    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.login(username="boss", password="pw")
        self.jazz = Tag.objects.create(name="Jazz nights", slug="jazz-nights")
        self.ada = Reader.objects.create(email="ada@example.com", location="London",
                                         interest_categories=["music"])
        self.ada.interest_tags.add(self.jazz)
        self.gig = event("Trio residency", tags=[self.jazz])
        self.other = event("Big band", tags=[self.jazz])
        self.base = {"r": str(self.ada.pk), "preview_reader": self.ada.pk,
                     "event": [self.gig.pk, self.other.pk]}

    def _post(self, **data):
        return self.client.post(reverse("desk:send"), {**self.base, **data}, follow=True)

    def _preview(self, **data):
        return self._post(action="preview", **data)

    def test_the_preview_offers_every_part_of_each_pick_to_edit(self):
        page = self._preview()
        for field in ("rationale", "hook", "caveat", "stars"):
            self.assertContains(page, f'name="{field}_{self.gig.pk}"')
        self.assertContains(page, 'name="intro"')
        self.assertContains(page, "Apply edits")
        self.assertFalse(any(p["edited"] for p in page.context["preview"]["picks"]))

    def test_applying_edits_shows_them_in_the_preview_and_marks_them(self):
        self._preview()
        page = self._post(action="save_edits",
                          **{f"rationale_{self.gig.pk}": "Forty seats, no amplification. Go.",
                             f"hook_{self.gig.pk}": "My best jazz bet for you this week."})
        self.assertContains(page, "Edits kept for ada@example.com")
        picks = {p["title"]: p for p in page.context["preview"]["picks"]}
        self.assertEqual(picks["Trio residency"]["rationale"], "Forty seats, no amplification. Go.")
        self.assertTrue(picks["Trio residency"]["edited"])
        self.assertFalse(picks["Big band"]["edited"])
        self.assertIn("Forty seats, no amplification", page.context["preview"]["html"])
        self.assertIn("My best jazz bet for you this week.", page.context["preview"]["html"])
        self.assertEqual(NewsletterIssue.objects.count(), 0)  # still only a draft

    def test_a_changed_rating_is_what_the_email_shows(self):
        self._preview()
        page = self._post(action="save_edits", **{f"stars_{self.other.pk}": "4.5"})
        picks = {p["title"]: p for p in page.context["preview"]["picks"]}
        self.assertEqual(picks["Big band"]["stars"], 4.5)
        self.assertIn("★★★★½", page.context["preview"]["html"])

    def test_edits_survive_previewing_someone_else_and_coming_back(self):
        bo = Reader.objects.create(email="bo@example.com", location="London",
                                   interest_categories=["music"])
        bo.interest_tags.add(self.jazz)
        both = {"r": f"{self.ada.pk},{bo.pk}"}
        self._preview(**both)
        self._post(action="save_edits", **both, **{f"rationale_{self.gig.pk}": "Ada's line."})
        self._preview(**both, preview_reader=bo.pk)
        page = self._preview(**both)
        picks = {p["title"]: p for p in page.context["preview"]["picks"]}
        self.assertEqual(picks["Trio residency"]["rationale"], "Ada's line.")
        # Bo's own draft was untouched by Ada's edit.
        page = self._preview(**both, preview_reader=bo.pk)
        self.assertFalse(any(p["edited"] for p in page.context["preview"]["picks"]))

    def test_the_send_sends_the_draft_as_read_without_writing_it_again(self):
        self._preview()
        self._post(action="save_edits", **{f"rationale_{self.gig.pk}": "My words, not AI's."})
        with mock.patch("recommendations.compose.compose") as compose_again:
            response = self._post(action="send")
        compose_again.assert_not_called()
        self.assertContains(response, "Sent to 1 user")
        recs = {r.opportunity.title: r for r in NewsletterIssue.objects.get().recommendations.all()}
        self.assertEqual(recs["Trio residency"].rationale, "My words, not AI's.")

    def test_a_user_without_a_draft_has_theirs_written_then_sent(self):
        response = self._post(action="send")
        self.assertContains(response, "Sent to 1 user")
        issue = NewsletterIssue.objects.get()
        self.assertIsNotNone(issue.sent_at)
        self.assertEqual(issue.recommendations.count(), 2)

    def test_write_it_again_drops_the_edits(self):
        self._preview()
        self._post(action="save_edits", **{f"rationale_{self.gig.pk}": "Mine."})
        page = self._post(action="discard_edits")
        self.assertContains(page, "again from scratch")
        self.assertFalse(any(p["edited"] for p in page.context["preview"]["picks"]))

    def test_an_emptied_box_keeps_what_was_written(self):
        self._preview()
        page = self._post(action="save_edits", **{f"rationale_{self.gig.pk}": "   "})
        picks = {p["title"]: p for p in page.context["preview"]["picks"]}
        self.assertTrue(picks["Trio residency"]["rationale"].strip())
        self.assertFalse(picks["Trio residency"]["edited"])

    def test_posting_every_box_marks_only_the_changed_one_as_edited(self):
        """A browser submits all the boxes; only a changed one is an edit."""
        page = self._preview()
        data = {"action": "save_edits"}
        for p in page.context["preview"]["picks"]:
            data[f"rationale_{p['event_id']}"] = p["rationale"]
            data[f"hook_{p['event_id']}"] = p["hook"]
            data[f"caveat_{p['event_id']}"] = p["caveat"]
            data[f"stars_{p['event_id']}"] = str(p["stars"])
        data[f"rationale_{self.gig.pk}"] = "Changed by hand."
        page = self._post(**data)
        after = {p["title"]: p for p in page.context["preview"]["picks"]}
        self.assertTrue(after["Trio residency"]["edited"])
        self.assertFalse(after["Big band"]["edited"])

    def test_editing_before_anything_is_written_says_to_preview_first(self):
        page = self._post(action="save_edits", **{f"rationale_{self.gig.pk}": "Too soon."})
        self.assertContains(page, "press Preview one first")
