"""A culture week: what goes in, where, rated how, and what the reader sees."""

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from opportunities import research, reviews
from opportunities.models import Category, CriticReview, Opportunity, Tag, stars_text
from readers.models import Reader
from recommendations import ai, compose, drafts, emailing
from recommendations.models import (IssueDraft, LinkClick, NewsletterIssue, ReaderReply,
                                    Recommendation)
from siteconfig.models import SiteConfig


def config(**overrides):
    cache.clear()
    c = SiteConfig.load()
    for k, v in overrides.items():
        setattr(c, k, v)
    c.save()
    cache.clear()
    return SiteConfig.load()


def tag(name, category=""):
    return Tag.objects.get_or_create(slug=name.replace(" ", "-"),
                                     defaults={"name": name, "category": category})[0]


def event(title, tags=(), category="music", **kw):
    data = dict(title=title, category=category, description=f"About {title}.",
                price_tier="budget", location_area="London", location_name="A room",
                booking_url=f"https://example.com/{title.replace(' ', '-')}",
                mainstream_to_unusual=3, intimate_to_large_scale=3,
                status=Opportunity.Status.PUBLISHED)
    data.update(kw)
    e = Opportunity.objects.create(**data)
    e.tags.add(*tags)
    return e


def reader(email="ada@example.com", tags=(), categories=("music",), **kw):
    r = Reader.objects.create(email=email, name=kw.pop("name", "Ada Lovelace"),
                              location="London", interest_categories=list(categories), **kw)
    r.interest_tags.add(*tags)
    return r


class TimingTests(TestCase):
    """How something sits against the week is part of recommending it."""

    def setUp(self):
        self.start = date(2026, 9, 17)
        self.end = self.start + timedelta(days=6)

    def timing(self, **kw):
        e = Opportunity(title="X", category=kw.pop("category", "theatre"), **kw)
        return compose.timing_for(e, self.start, self.end, 120)

    def test_closing_this_week_is_last_chance(self):
        t = self.timing(start_date=date(2026, 8, 1), end_date=date(2026, 9, 19))
        self.assertEqual(t, ("last_chance", "Final week: closes Sat 19 Sep"))

    def test_closing_today_says_so(self):
        self.assertEqual(self.timing(end_date=self.start)[1], "Last chance: closes today")

    def test_a_one_night_gig_in_the_week(self):
        self.assertEqual(self.timing(start_date=date(2026, 9, 20)), ("one_night", "Sun 20 Sep"))

    def test_opening_this_week(self):
        t = self.timing(start_date=date(2026, 9, 18), end_date=date(2026, 11, 1))
        self.assertEqual(t, ("new", "Opens Fri 18 Sep"))

    def test_a_long_run_says_there_is_no_rush(self):
        t = self.timing(start_date=date(2026, 6, 1), end_date=date(2027, 7, 1))
        self.assertEqual(t, ("on", "On until 1 July 2027"))

    def test_later_goes_in_book_ahead_and_much_later_is_left_out(self):
        self.assertEqual(self.timing(start_date=date(2026, 10, 13))[0], "book_ahead")
        self.assertIsNone(self.timing(start_date=date(2027, 6, 1)))

    def test_ended_is_left_out(self):
        self.assertIsNone(self.timing(end_date=date(2026, 9, 1)))

    def test_releases_are_news_in_their_week_not_before(self):
        self.assertEqual(self.timing(category="book", start_date=date(2026, 9, 18)),
                         ("release", "Out Fri 18 Sep"))
        self.assertEqual(self.timing(category="listen", start_date=date(2026, 9, 10))[0], "release")
        self.assertIsNone(self.timing(category="watch", start_date=date(2026, 10, 1)))

    def test_week_label_reads_like_a_newsletter(self):
        self.assertEqual(compose.week_label(date(2026, 9, 17), date(2026, 9, 23)),
                         "Thursday 17–Wednesday 23 September")
        self.assertEqual(compose.week_label(date(2026, 8, 30), date(2026, 9, 5)),
                         "Sunday 30 August–Saturday 5 September")


class StarsTests(TestCase):
    def test_half_stars(self):
        self.assertEqual(stars_text(4.5), "★★★★½")
        self.assertEqual(stars_text(Decimal("3.0")), "★★★")
        self.assertEqual(stars_text(None), "")

    def test_score_bands(self):
        self.assertEqual(compose.stars_for_score(6.2), 5.0)
        self.assertEqual(compose.stars_for_score(3.5), 3.5)
        self.assertEqual(compose.stars_for_score(2.5), 3.0)
        self.assertEqual(compose.stars_for_score(1.5), 2.0)
        self.assertEqual(compose.stars_for_score(-1), 1.0)


class ChoosingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.jazz = tag("jazz", "music")
        self.art = tag("contemporary art", "exhibition")
        self.ada = reader(tags=[self.jazz, self.art], categories=["music", "exhibition"])

    def test_only_picks_that_reach_the_floor_go_in(self):
        event("Strong", tags=[self.jazz])                       # tag + category
        event("Category only")                                   # 2 stars: out
        issue = compose.compose(self.ada, use_ai=False)
        self.assertEqual([p.opportunity.title for p in issue.picks], ["Strong"])

    def test_the_top_takes_no_more_than_two_from_one_category(self):
        for i in range(4):
            event(f"Gig {i}", tags=[self.jazz])
        for i in range(3):
            event(f"Show {i}", tags=[self.art], category="exhibition")
        issue = compose.compose(self.ada, use_ai=False)
        top = issue.top
        self.assertEqual(len(top), 4)
        self.assertLessEqual(sum(1 for p in top if p.section == "music"), 2)
        self.assertLessEqual(sum(1 for p in top if p.section == "exhibition"), 2)
        # Top first, then sections in the email's order.
        self.assertTrue(all(p.is_top for p in issue.picks[:4]))
        sections = [p.section for p in issue.picks[4:]]
        self.assertEqual(sections, sorted(sections, key=compose.SECTION_ORDER.get))

    def test_each_section_below_the_top_is_capped(self):
        config(max_per_section=1, top_picks_count=1)
        for i in range(5):
            event(f"Gig {i}", tags=[self.jazz])
        issue = compose.compose(self.ada, use_ai=False)
        self.assertEqual(sum(1 for p in issue.picks if not p.is_top and p.section == "music"), 1)

    def test_something_further_off_goes_in_book_ahead(self):
        later = timezone.localdate() + timedelta(days=40)
        event("Stevie at the O2", tags=[self.jazz], start_date=later)
        issue = compose.compose(self.ada, use_ai=False)
        self.assertEqual(issue.picks[0].section, "book_ahead")
        self.assertFalse(issue.picks[0].is_top)

    def test_saved_comes_back_inside_the_cooldown_and_booked_never_does(self):
        saved = event("Saved show", tags=[self.art], category="exhibition")
        booked = event("Booked gig", tags=[self.jazz])
        old = NewsletterIssue.objects.create(reader=self.ada, sent_at=timezone.now())
        Recommendation.objects.create(issue=old, opportunity=saved, rationale="x",
                                      feedback=Recommendation.Feedback.SAVE)
        Recommendation.objects.create(issue=old, opportunity=booked, rationale="x",
                                      feedback=Recommendation.Feedback.BOOKED)
        issue = compose.compose(self.ada, use_ai=False)
        by_title = {p.opportunity.title: p for p in issue.picks}
        self.assertIn("Saved show", by_title)
        self.assertEqual(by_title["Saved show"].section, "saved")
        self.assertNotIn("Booked gig", by_title)

    def test_one_wildcard_only_for_someone_who_asked_to_be_surprised(self):
        event("Gig", tags=[self.jazz])
        event("Odd thing", category="music")   # no shared interest
        self.assertFalse(any(p.wildcard for p in compose.compose(self.ada, use_ai=False).picks))
        self.ada.open_to_surprise = True
        self.ada.save()
        picks = compose.compose(self.ada, use_ai=False).picks
        self.assertEqual([p.opportunity.title for p in picks if p.wildcard], ["Odd thing"])


class WritingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.jazz = tag("jazz", "music")
        self.ada = reader(tags=[self.jazz])
        self.gig = event("Gig", tags=[self.jazz])
        self.other = event("Other gig", tags=[self.jazz])

    def written(self, **extra):
        return {
            "intro": "A strong week for you.",
            "picks": [
                {"event_id": self.gig.pk, "for_you_stars": 5, "hook": "Go.",
                 "what_it_is": "A trio.", "why_for_you": "You said small rooms.",
                 "caveat": "Two hours."},
            ],
            "programme": [{"day": "Friday 18", "plan": "The gig."}, {"day": "x", "plan": ""}],
            "closing": "Gig first.",
            **extra,
        }

    def test_ai_writes_the_week_and_may_move_a_rating_by_one_star_only(self):
        with mock.patch.object(ai, "write_issue", return_value=self.written()):
            issue = compose.compose(self.ada)
        gig = next(p for p in issue.picks if p.opportunity == self.gig)
        self.assertEqual(gig.stars, 4.5)       # scored 3.5, asked 5, held to +1
        self.assertEqual(gig.rationale, "A trio.\n\nYou said small rooms.")
        self.assertEqual((gig.hook, gig.caveat), ("Go.", "Two hours."))
        self.assertEqual(issue.intro, "A strong week for you.")
        self.assertEqual(issue.programme, [{"day": "Friday 18", "plan": "The gig."}])
        # A pick the model skipped still has words.
        other = next(p for p in issue.picks if p.opportunity == self.other)
        self.assertTrue(other.rationale)
        self.assertTrue(issue.written_by_ai)

    def test_a_pick_ai_rates_below_the_floor_is_dropped(self):
        written = self.written()
        written["picks"].append({"event_id": self.other.pk, "for_you_stars": 2.5, "hook": "",
                                 "what_it_is": "x", "why_for_you": "y", "caveat": ""})
        with mock.patch.object(ai, "write_issue", return_value=written):
            issue = compose.compose(self.ada)
        self.assertEqual([p.opportunity for p in issue.picks], [self.gig])

    @override_settings(OPENAI_API_KEY="test-key")
    def test_the_prompt_carries_only_verified_reviews_and_the_readers_replies(self):
        CriticReview.objects.create(opportunity=self.gig, publication="The Guardian",
                                    stars=4, url="https://www.theguardian.com/x",
                                    verified=CriticReview.Verified.PAGE)
        CriticReview.objects.create(opportunity=self.gig, publication="Time Out",
                                    stars=2, url="https://www.timeout.com/x")
        ReaderReply.objects.create(reader=self.ada, text="Tribute nights aren't for me.")
        with mock.patch.object(ai, "_call", return_value=None) as call:
            compose.compose(self.ada)
        system, user = call.call_args.args[:2]
        self.assertIn("may never invent a\n  review, a star rating", system)
        self.assertIn("The Guardian: 4 stars out of 5", user)
        self.assertNotIn("Time Out", user)
        self.assertIn("Tribute nights aren't for me.", user)


class StoringAndRenderingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.jazz = tag("jazz", "music")
        self.ada = reader(tags=[self.jazz])
        self.gig = event("Ancient Infinity Orchestra", tags=[self.jazz],
                         end_date=timezone.localdate() + timedelta(days=2),
                         start_date=timezone.localdate() - timedelta(days=10))
        self.review = CriticReview.objects.create(
            opportunity=self.gig, publication="Time Out", stars=Decimal("4.5"),
            url="https://www.timeout.com/review", quote="Fun and profound",
            verified=CriticReview.Verified.PAGE)
        CriticReview.objects.create(opportunity=self.gig, publication="The Times", stars=2,
                                    url="https://www.thetimes.com/review")

    def composed(self):
        issue = compose.compose(self.ada, use_ai=False)
        issue.intro = "Filtered more tightly this week."
        issue.programme = [{"day": "Thursday 17", "plan": "The orchestra."}]
        issue.closing = "The orchestra first."
        issue.picks[0].stars = 4.5
        return issue

    def test_materialise_keeps_everything_the_email_needs(self):
        composed = self.composed()
        stored = compose.materialise(composed)
        rec = stored.recommendations.get()
        self.assertEqual(stored.intro, "Filtered more tightly this week.")
        self.assertEqual(rec.for_you_rating, Decimal("4.5"))
        self.assertEqual(rec.fit_display, "★★★★½")
        self.assertEqual(rec.timing, "last_chance")
        self.assertTrue(rec.is_top)
        self.assertEqual(str(rec.feedback_token), composed.picks[0].token)

    def test_the_email_is_a_culture_week(self):
        stored = compose.materialise(self.composed())
        subject, html, text = emailing.render_newsletter(stored)
        self.assertIn("Ada’s culture week:", subject)
        self.assertIn(compose.week_label(stored.week_start, stored.week_end), subject)
        for part in ("The ones I&#x27;d put at the top", "Filtered more tightly this week.",
                     "FOR YOU", "★★★★½", "Time Out ★★★★½", "Final week: closes",
                     "If I were programming your week", "My strongest bets this week",
                     "Fun and profound", "Tell me what you thought of this week"):
            self.assertIn(part, html)
        # An unverified review is never printed.
        self.assertNotIn("The Times", html)
        rec = stored.recommendations.get()
        self.assertIn(reverse("recommendations:review-click",
                              kwargs={"token": rec.feedback_token, "review_id": self.review.pk}), html)
        self.assertIn("Time Out ★★★★½", text)

    def test_a_preview_links_straight_to_the_venue_and_the_review(self):
        _, html, _ = emailing.render_composed(self.composed(), tracked=False)
        self.assertIn("https://www.timeout.com/review", html)
        self.assertIn(self.gig.booking_url, html)

    def test_an_issue_from_before_sections_still_renders(self):
        issue = NewsletterIssue.objects.create(reader=self.ada)
        Recommendation.objects.create(issue=issue, opportunity=self.gig, rationale="Old words.",
                                      score=4.2)
        _, html, _ = emailing.render_newsletter(issue)
        self.assertIn("Old words.", html)
        self.assertIn("★★★★", html)


@override_settings(SITE_BASE_URL="https://ether.test")
class ReaderLinksTests(TestCase):
    def setUp(self):
        self.jazz = tag("jazz", "music")
        self.ada = reader(tags=[self.jazz])
        self.gig = event("Gig", tags=[self.jazz])
        self.issue = NewsletterIssue.objects.create(reader=self.ada, sent_at=timezone.now())
        self.rec = Recommendation.objects.create(issue=self.issue, opportunity=self.gig,
                                                 rationale="x")
        self.review = CriticReview.objects.create(
            opportunity=self.gig, publication="The Guardian", stars=4,
            url="https://www.theguardian.com/music/review", verified="page")

    def test_a_review_link_is_recorded_then_opens_the_review(self):
        url = reverse("recommendations:review-click",
                      kwargs={"token": self.rec.feedback_token, "review_id": self.review.pk})
        response = self.client.get(url)
        self.assertRedirects(response, self.review.url, fetch_redirect_response=False)
        self.assertEqual(LinkClick.objects.get().section, LinkClick.Section.REVIEW)

    def test_a_review_of_another_event_cannot_be_reached_through_this_pick(self):
        other = CriticReview.objects.create(opportunity=event("Else"), publication="Time Out",
                                            url="https://evil.example.com/", verified="page")
        url = reverse("recommendations:review-click",
                      kwargs={"token": self.rec.feedback_token, "review_id": other.pk})
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_reader_can_say_why_about_a_pick(self):
        url = reverse("recommendations:reply", kwargs={"token": self.rec.feedback_token})
        self.assertContains(self.client.get(url), "What did you think?")
        response = self.client.post(url, {"text": "Original artists, not tribute nights."})
        self.assertContains(response, "Thank you.")
        reply = ReaderReply.objects.get()
        self.assertEqual((reply.reader, reply.recommendation), (self.ada, self.rec))

    def test_a_reader_can_reply_about_the_whole_week(self):
        url = reverse("recommendations:reply", kwargs={"token": self.rec.feedback_token})
        self.client.post(url + "?about=week", {"text": "More small rooms.", "about": "week"})
        reply = ReaderReply.objects.get()
        self.assertIsNone(reply.recommendation)
        self.assertEqual(reply.issue, self.issue)

    def test_an_empty_reply_is_not_kept(self):
        url = reverse("recommendations:reply", kwargs={"token": self.rec.feedback_token})
        self.client.post(url, {"text": "   "})
        self.assertFalse(ReaderReply.objects.exists())

    def test_the_feedback_page_asks_for_a_reason(self):
        response = self.client.get(reverse("recommendations:feedback",
                                           kwargs={"token": self.rec.feedback_token,
                                                   "action": "not-for-me"}))
        self.assertContains(response, "Why it isn't for you helps most")


GUARDIAN_PAGE = """<html><head><title>Gig review</title>
<script type="application/ld+json">{"@type": "Review", "reviewRating":
 {"@type": "Rating", "ratingValue": "4", "bestRating": "5"}}</script></head>
<body><h1>Ancient Infinity Orchestra review - spiritual jazz</h1>
<p>Published 2/5/2026</p></body></html>"""


class ReviewCheckTests(TestCase):
    def setUp(self):
        self.gig = event("Ancient Infinity Orchestra")

    def review(self, **kw):
        data = dict(opportunity=self.gig, publication="The Guardian", stars=4,
                    url="https://www.theguardian.com/music/2026/sep/03/aio-review")
        data.update(kw)
        return CriticReview(**data)

    def test_a_rating_confirmed_on_the_page_is_verified(self):
        r = reviews.check(self.review(), fetcher=lambda url: (200, GUARDIAN_PAGE))
        self.assertEqual(r.verified, CriticReview.Verified.PAGE)
        self.assertEqual(r.stars, Decimal("4"))

    def test_the_page_corrects_a_wrong_rating(self):
        r = reviews.check(self.review(stars=5), fetcher=lambda url: (200, GUARDIAN_PAGE))
        self.assertEqual(r.stars, Decimal("4"))
        self.assertIn("the page says 4", r.check_note)

    def test_a_link_off_the_publications_site_is_not_verified(self):
        r = reviews.check(self.review(url="https://blog.example.com/aio"),
                          fetcher=lambda url: (200, GUARDIAN_PAGE))
        self.assertEqual(r.verified, "")
        self.assertIn("own site", r.check_note)

    def test_a_page_we_cannot_read_waits_for_an_editor(self):
        r = reviews.check(self.review(publication="The Times", url="https://www.thetimes.com/x"),
                          fetcher=lambda url: (403, ""))
        self.assertEqual(r.verified, "")
        self.assertIn("paywall", r.check_note)

    def test_a_page_about_something_else_is_not_verified(self):
        other = GUARDIAN_PAGE.replace("Ancient Infinity Orchestra", "A different band")
        r = reviews.check(self.review(), fetcher=lambda url: (200, other))
        self.assertEqual(r.verified, "")

    def test_stars_we_cannot_find_are_not_printed(self):
        page = "<h1>Ancient Infinity Orchestra</h1><p>A fine night.</p>"
        r = reviews.check(self.review(), fetcher=lambda url: (200, page))
        self.assertEqual(r.verified, "")
        self.assertIn("Check it by hand", r.check_note)

    def test_an_editors_verification_is_left_alone(self):
        r = reviews.check(self.review(verified=CriticReview.Verified.EDITOR),
                          fetcher=lambda url: (500, ""))
        self.assertEqual(r.verified, CriticReview.Verified.EDITOR)

    def test_rating_formats_and_a_date_that_is_not_one(self):
        self.assertEqual(reviews.ratings_on_page("<p>4 out of 5 stars</p>"), [4.0])
        self.assertEqual(reviews.ratings_on_page("<span>★★★☆☆</span>"), [3.0])
        self.assertEqual(reviews.ratings_on_page("<p>Rating: 3.5/5</p>"), [3.5])
        self.assertEqual(reviews.ratings_on_page("<p>on 2/5 we went</p>"), [])

    def test_found_reviews_are_stored_once_per_publication_and_checked(self):
        found = [
            {"publication": "Guardian", "stars": 4, "url": "https://www.theguardian.com/a", "quote": ""},
            {"publication": "The Guardian", "stars": 5, "url": "https://www.theguardian.com/b", "quote": ""},
            {"publication": "Time Out", "stars": None, "url": "not a url", "quote": ""},
        ]
        created = reviews.save_found(self.gig, found, fetcher=lambda url: (200, GUARDIAN_PAGE))
        self.assertEqual([r.publication for r in created], ["The Guardian"])
        self.assertTrue(created[0].is_verified)


class ReleaseDraftsTests(TestCase):
    def test_a_release_is_saved_as_something_you_do_not_travel_to(self):
        payload = {"listings": [{
            "title": "The Disappearers", "description": "A novel.", "category": "book",
            "price_tier": "budget", "price_display": "£22", "location_name": "Hamish Hamilton",
            "location_area": "", "booking_url": "https://example.org/book",
            "start_date": "2026-09-03", "end_date": "", "mainstream_to_unusual": 3,
            "intimate_to_large_scale": 3, "tags": [],
            "sources": [{"title": "Publisher", "url": "https://example.org/book"}]}]}
        created = research.save_drafts(payload)
        book = created[0]
        self.assertEqual(book.category, Category.BOOK)
        self.assertTrue(book.is_online)
        self.assertEqual(book.location_area, "UK")
        self.assertEqual(book.status, Opportunity.Status.DRAFT)


class DraftTests(TestCase):
    def setUp(self):
        cache.clear()
        self.jazz = tag("jazz", "music")
        self.ada = reader(tags=[self.jazz])

    def test_nothing_good_enough_is_a_skipped_draft_that_says_why(self):
        event("Category only")
        draft = drafts.build(self.ada)
        draft.refresh_from_db()
        self.assertEqual(draft.status, IssueDraft.Status.EMPTY)
        self.assertIn("Skipped", draft.message)

    def test_a_ready_draft_is_sent_as_it_stands(self):
        event("Gig", tags=[self.jazz])
        draft = drafts.build(self.ada)
        draft.refresh_from_db()
        self.assertEqual(draft.status, IssueDraft.Status.READY)
        with mock.patch("recommendations.compose.compose") as again:
            drafts.send_to([self.ada])
        again.assert_not_called()
        draft = drafts.latest(self.ada)
        self.assertEqual(draft.status, IssueDraft.Status.SENT)
        self.assertIsNotNone(draft.issue.sent_at)

    def test_a_draft_stuck_writing_is_marked_failed(self):
        draft = IssueDraft.objects.create(reader=self.ada, status=IssueDraft.Status.BUILDING)
        IssueDraft.objects.filter(pk=draft.pk).update(
            updated_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(drafts.latest(self.ada).status, IssueDraft.Status.FAILED)


class DeskReviewsTests(TestCase):
    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
        self.client.login(username="boss", password="pw")
        self.gig = event("Gig")
        self.url = reverse("desk:listings_reviews", args=[self.gig.pk])

    def test_an_editor_adds_a_review_and_it_is_verified(self):
        self.client.post(self.url, {"action": "add", "publication": "guardian", "stars": "4",
                                    "url": "https://www.theguardian.com/x"})
        review = CriticReview.objects.get()
        self.assertEqual(review.publication, "The Guardian")
        self.assertEqual(review.verified, CriticReview.Verified.EDITOR)

    def test_verify_take_out_and_remove(self):
        review = CriticReview.objects.create(opportunity=self.gig, publication="Time Out",
                                             url="https://www.timeout.com/x")
        self.client.post(self.url, {"action": "verify", "review": review.pk})
        review.refresh_from_db()
        self.assertTrue(review.is_verified)
        self.client.post(self.url, {"action": "unverify", "review": review.pk})
        review.refresh_from_db()
        self.assertFalse(review.is_verified)
        self.client.post(self.url, {"action": "delete", "review": review.pk})
        self.assertFalse(CriticReview.objects.exists())

    def test_the_event_page_shows_the_reviews_panel(self):
        page = self.client.get(reverse("desk:listings_change", args=[self.gig.pk]))
        self.assertContains(page, "Critic reviews")
        self.assertContains(page, "Find reviews with AI")

    def test_find_starts_a_search(self):
        with mock.patch("opportunities.reviews.start", return_value=True) as start:
            self.client.post(self.url, {"action": "find"})
        start.assert_called_once_with(self.gig)

    def test_the_events_page_offers_this_weeks_releases(self):
        page = self.client.get(reverse("desk:listings_list"))
        self.assertContains(page, "Find this week's releases")
        with mock.patch("recommendations.ai.is_enabled", return_value=True), \
             mock.patch("opportunities.research.start_releases", return_value=True):
            response = self.client.post(reverse("desk:listings_list"),
                                        {"action": "find_releases"}, follow=True)
        self.assertContains(response, "this week&#x27;s new books, albums and TV")

    def test_settings_show_the_shape_of_a_culture_week(self):
        page = self.client.get(reverse("desk:siteconfig"))
        for field in ("min_for_you_stars", "top_picks_count", "max_per_section",
                      "book_ahead_max", "carry_forward_saved", "ai_issue_timeout_seconds"):
            self.assertContains(page, f'name="{field}"')
