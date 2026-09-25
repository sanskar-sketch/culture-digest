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
from readers.models import InterestPreference, Reader
from recommendations import ai, compose, drafts, emailing, matching
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


class BreadthTests(TestCase):
    """A week is a week, not twenty of whatever scores highest."""

    def setUp(self):
        cache.clear()
        self.config = config(recommendations_per_send=6, max_per_section=4)
        tags = [tag("new albums", "listen"), tag("jazz", "music"), tag("plays", "theatre")]
        self.reader = reader(tags=tags, categories=("listen", "music", "theatre"))
        # Releases carry more matching tags than a gig does, so on score
        # alone they would take every slot.
        for n in range(6):
            event(f"Album {n}", tags=tags, category="listen")
        self.gig = event("A gig", tags=[tags[1]], category="music")
        self.play = event("A play", tags=[tags[2]], category="theatre")

    def test_every_section_gives_up_its_best_before_one_gives_up_its_second(self):
        picks = compose.shortlist(
            compose.gather(self.reader, compose.issue_week(), self.config), self.config, self.reader)
        titles = [p.opportunity.title for p in picks]
        self.assertIn("A gig", titles)
        self.assertIn("A play", titles)
        self.assertEqual(len(titles), 6)
        self.assertEqual(sum(1 for t in titles if t.startswith("Album")), 4)

    def test_a_pile_of_tags_does_not_beat_a_square_match(self):
        from recommendations import matching
        many = matching.score_opportunity(self.reader, Opportunity.objects.get(title="Album 0"),
                                          {}, self.config)
        one = matching.score_opportunity(self.reader, self.gig, {}, self.config)
        # Three matching tags still beat one, but by less than three times.
        self.assertGreater(many.score, one.score)
        self.assertLessEqual(many.score - one.score, self.config.weight_tag_overlap)


class PlaceTests(TestCase):
    """Where a thing is, as research actually writes it down."""

    def setUp(self):
        cache.clear()
        self.jazz = tag("jazz", "music")
        self.reader = reader(tags=[self.jazz], travel_radius=Reader.TravelRadius.WITHIN_CITY)

    def picks(self, area):
        event("A gig", tags=[self.jazz], location_area=area)
        got = compose.gather(self.reader, compose.issue_week(), SiteConfig.load())
        Opportunity.objects.all().delete()
        return got

    def test_a_neighbourhood_is_still_the_city(self):
        for area in ("London", "Camden, London", "Soho, London", "london"):
            self.assertEqual(len(self.picks(area)), 1, area)

    def test_another_city_is_not(self):
        self.assertEqual(self.picks("Manchester"), [])


class PerInterestSettingsTests(TestCase):
    """What a reader will travel and spend, interest by interest."""

    def setUp(self):
        cache.clear()
        self.config = config()
        self.jazz = tag("jazz", "music")
        self.art = tag("contemporary art", "exhibition")
        self.reader = reader(tags=[self.jazz, self.art], categories=("music", "exhibition"),
                             travel_radius=Reader.TravelRadius.LOCAL_ONLY,
                             budget=Reader.Budget.FREE_CHEAP)

    def exception(self, tag_obj, **fields):
        InterestPreference.objects.create(reader=self.reader, tag=tag_obj, **fields)
        self.reader = Reader.objects.get(pk=self.reader.pk)   # drop the cached read

    def score(self, opportunity):
        return matching.score_opportunity(self.reader, opportunity, {}, self.config)

    def test_a_pricey_gig_is_out_until_they_say_they_would_spend_it_on_jazz(self):
        gig = event("Late set", tags=[self.jazz], price_tier="splurge")
        self.assertIsNone(self.score(gig))
        self.exception(self.jazz, budget=Reader.Budget.NO_LIMIT)
        self.assertIsNotNone(self.score(gig))

    def test_they_will_travel_for_jazz_but_not_for_art(self):
        away_gig = event("Gig out of town", tags=[self.jazz], location_area="Margate")
        away_show = event("Show out of town", tags=[self.art], category="exhibition",
                          location_area="Margate")
        self.exception(self.jazz, travel_radius=Reader.TravelRadius.ANYWHERE)
        self.assertIsNotNone(self.score(away_gig))
        self.assertIsNone(self.score(away_show))

    def test_where_two_exceptions_meet_the_more_willing_one_wins(self):
        both = event("Jazz in a gallery", tags=[self.jazz, self.art], price_tier="splurge")
        self.exception(self.jazz, budget=Reader.Budget.NO_LIMIT)
        self.exception(self.art, budget=Reader.Budget.FREE_CHEAP)
        self.assertIsNotNone(self.score(both))

    def test_the_taste_dials_average_where_two_apply(self):
        self.exception(self.jazz, scale_preference=1)
        self.exception(self.art, scale_preference=5)
        prefs = matching.preferences_for(self.reader, event("Both", tags=[self.jazz, self.art]))
        self.assertEqual(prefs.scale_preference, 3)

    def test_an_interest_with_no_exception_keeps_their_usual_answers(self):
        self.exception(self.jazz, budget=Reader.Budget.NO_LIMIT)
        prefs = matching.preferences_for(self.reader, event("Show", tags=[self.art],
                                                            category="exhibition"))
        self.assertEqual((prefs.budget, prefs.travel_radius),
                         (Reader.Budget.FREE_CHEAP, Reader.TravelRadius.LOCAL_ONLY))

    def test_a_whole_category_covers_interests_they_never_picked_out(self):
        gig = event("Late set", tags=[], price_tier="splurge")   # music, no tags
        self.assertIsNone(self.score(gig))
        InterestPreference.objects.create(reader=self.reader, category="music",
                                          budget=Reader.Budget.NO_LIMIT)
        self.reader = Reader.objects.get(pk=self.reader.pk)
        self.assertIsNotNone(self.score(gig))

    def test_an_interest_beats_its_category_but_the_category_fills_the_gaps(self):
        InterestPreference.objects.create(reader=self.reader, category="music",
                                          budget=Reader.Budget.NO_LIMIT,
                                          travel_radius=Reader.TravelRadius.LOCAL_ONLY,
                                          scale_preference=5)
        self.exception(self.jazz, travel_radius=Reader.TravelRadius.ANYWHERE)
        prefs = matching.preferences_for(self.reader, event("Jazz night", tags=[self.jazz]))
        # The interest answers travel; music answers what it didn't.
        self.assertEqual(prefs.travel_radius, Reader.TravelRadius.ANYWHERE)
        self.assertEqual(prefs.budget, Reader.Budget.NO_LIMIT)
        self.assertEqual(prefs.scale_preference, 5)

    def rank(self, **ranks):
        for obj, position in ranks.values():
            InterestPreference.objects.update_or_create(reader=self.reader, tag=obj,
                                                        defaults={"rank": position})
        self.reader = Reader.objects.get(pk=self.reader.pk)

    def test_what_they_ranked_first_scores_higher(self):
        self.reader.travel_radius = ""
        self.reader.budget = ""
        self.reader.save()
        self.rank(jazz=(self.jazz, 1), art=(self.art, 2))
        gig = self.score(event("A gig", tags=[self.jazz]))
        show = self.score(event("A show", tags=[self.art], category="exhibition"))
        self.assertAlmostEqual(gig.score - show.score, self.config.weight_interest_rank)
        self.assertIn("jazz is what they ranked first", gig.reasons)

    def test_the_email_leads_with_what_they_ranked_first(self):
        self.reader.travel_radius = ""
        self.reader.budget = ""
        self.reader.save()
        theatre = tag("plays", "theatre")
        self.reader.interest_tags.add(theatre)
        self.reader.interest_categories = ["music", "exhibition", "theatre"]
        self.reader.save()
        # Art first, then plays, then jazz - the reverse of the usual order.
        self.rank(art=(self.art, 1), plays=(theatre, 2), jazz=(self.jazz, 3))
        for n in range(3):
            event(f"Show {n}", tags=[self.art], category="exhibition")
            event(f"Play {n}", tags=[theatre], category="theatre")
            event(f"Gig {n}", tags=[self.jazz])
        issue = compose.compose(self.reader, use_ai=False)
        # Rated alike, the top goes to their first-ranked interest.
        self.assertEqual(issue.top[0].opportunity.category, "exhibition")
        # Below the top, sections come in their order too - not Music first.
        views = [{"is_top": p.is_top, "section": p.section} for p in issue.picks]
        order = [g["key"] for g in emailing._sections(views)]
        self.assertEqual(order, ["top", "exhibition", "theatre", "music"])
        _, html, _ = emailing.render_composed(issue)
        self.assertLess(html.find("Art &amp; exhibitions"), html.find("Live music"))

    def test_the_writer_sees_the_ranking_and_their_own_words(self):
        self.reader.loved_examples = "A late set at the Vortex."
        self.reader.disliked_examples = "Tribute acts."
        self.reader.notes = "Taking my mum."
        self.reader.save()
        self.rank(art=(self.art, 1), jazz=(self.jazz, 2))
        context = ai._reader_context(self.reader)
        self.assertIn("1. contemporary art; 2. jazz", context)
        for said in ("A late set at the Vortex.", "Tribute acts.", "Taking my mum."):
            self.assertIn(said, context)
        pick = compose.Pick(opportunity=event("Gig", tags=[self.jazz]), score=1, reasons=[],
                            timing="on", timing_label="", section="music", stars=4)
        self.assertEqual(ai._item_for_writing(pick, "full", self.reader)["their_ranking"],
                         "jazz: ranked 2 of 2")
        for rule in ("Loved:", "Not for them:", "Anything else:", "THE ORDER THEY PUT THINGS IN"):
            self.assertIn(rule, ai.PICKS_SYSTEM)

    def test_the_writer_is_told_about_the_exceptions(self):
        self.exception(self.jazz, travel_radius=Reader.TravelRadius.ANYWHERE,
                       budget=Reader.Budget.TREAT)
        context = ai._reader_context(self.reader)
        self.assertIn("Exceptions they set", context)
        self.assertIn("jazz: i'll travel far for something special, happy to treat myself",
                      context.lower())


class EveryInterestTests(TestCase):
    """Someone who ticked five things should hear about all five."""

    def setUp(self):
        cache.clear()
        self.config = config(recommendations_per_send=6, max_per_section=4)
        self.tags = {name: tag(name, cat) for name, cat in [
            ("jazz", "music"), ("live gigs", "music"), ("photography", "exhibition"),
            ("contemporary art", "exhibition"), ("comedy", "event")]}
        self.reader = reader(tags=list(self.tags.values()),
                             categories=("music", "exhibition", "other"))
        # A rich seam of jazz, and one thing each for the rest.
        for n in range(6):
            event(f"Jazz night {n}", tags=[self.tags["jazz"]])
        event("A big gig", tags=[self.tags["live gigs"]])
        event("Photo show", tags=[self.tags["photography"]], category="exhibition")
        event("Painting show", tags=[self.tags["contemporary art"]], category="exhibition")
        event("Comedy night", tags=[self.tags["comedy"]], category="other")

    def test_each_interest_gets_its_best_before_one_gets_a_second(self):
        picks = compose.shortlist(
            compose.gather(self.reader, compose.issue_week(), self.config), self.config, self.reader)
        covered = {t.name for p in picks for t in p.opportunity.tags.all()}
        self.assertEqual(covered, set(self.tags))
        self.assertLessEqual(sum(1 for p in picks if p.opportunity.title.startswith("Jazz")), 2)

    @override_settings(OPENAI_API_KEY="test-key")
    def test_the_writer_is_told_which_interests_drew_nothing(self):
        Opportunity.objects.filter(title__in=["Photo show", "Comedy night"]).delete()
        def answer(system, user, schema, name, **kw):
            if name != "culture_week_picks":
                return None      # the frame falls back; we only want its prompt
            items = json.loads(user.split("as JSON:\n", 1)[1])
            return {"picks": [{"event_id": i["event_id"], "for_you_stars": 4, "hook": "Go.",
                               "what_it_is": "A thing.", "why_for_you": "Yours.", "caveat": ""}
                              for i in items]}

        with mock.patch.object(ai, "_call", side_effect=answer) as call:
            compose.compose(self.reader)
        frames = [c for c in call.call_args_list if c.args[3] == "culture_week_frame"]
        self.assertTrue(frames, "the frame is written once the picks are in")
        user = frames[-1].args[1]
        self.assertIn("Interests of theirs with nothing worth sending this week:", user)
        self.assertIn("photography", user.split("The picks")[0])
        self.assertIn("comedy", user.split("The picks")[0])


class SportSectionTests(TestCase):
    def test_a_fixture_goes_out_under_sport(self):
        cache.clear()
        football = Tag.objects.get(slug="football")
        fan = reader(tags=[football], categories=("sport",))
        event("Derby day", tags=[football], category="sport",
              start_date=timezone.localdate() + timedelta(days=2))
        issue = compose.compose(fan, use_ai=False)
        self.assertEqual([p.section for p in issue.picks], ["sport"])
        _, html, _ = emailing.render_composed(issue)
        self.assertIn("Derby day", html)


class WritingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.jazz = tag("jazz", "music")
        self.ada = reader(tags=[self.jazz])
        self.gig = event("Gig", tags=[self.jazz])
        self.other = event("Other gig", tags=[self.jazz])

    def row(self, e, stars=5, **kw):
        return {"event_id": e.pk, "for_you_stars": stars, "hook": "Go.", "what_it_is": "A trio.",
                "why_for_you": "You said small rooms.", "caveat": "Two hours.", **kw}

    def frame(self, **kw):
        return {"intro": "A strong week for you.", "section_notes": [], "programme": [],
                "strongest": [self.gig.pk], "closing": "Gig first.", **kw}

    def compose_with(self, rows, frame=None, **kw):
        with mock.patch.object(ai, "write_picks", return_value=rows), \
                mock.patch.object(ai, "write_frame", return_value=frame):
            return compose.compose(self.ada, **kw)

    def test_ai_writes_the_week_and_may_move_a_rating_by_one_star_only(self):
        issue = self.compose_with([self.row(self.gig), self.row(self.other, stars=3.5)], self.frame())
        gig = next(p for p in issue.picks if p.opportunity == self.gig)
        self.assertEqual(gig.stars, 4.5)       # scored 3.5, asked 5, held to +1
        self.assertEqual(gig.rationale, "A trio.\n\nYou said small rooms.")
        self.assertEqual((gig.hook, gig.caveat), ("Go.", "Two hours."))
        self.assertEqual((issue.intro, issue.closing), ("A strong week for you.", "Gig first."))
        self.assertEqual(issue.strongest, [self.gig.pk])
        self.assertTrue(issue.written_by_ai)

    def test_a_pick_ai_would_not_write_is_left_out_not_sent_in_other_words(self):
        issue = self.compose_with([self.row(self.gig)], self.frame())
        self.assertEqual([p.opportunity for p in issue.picks], [self.gig])
        self.assertIn("AI didn't write up 1 more, so it was left out.", issue.message)

    def test_a_pick_ai_rates_below_the_floor_is_dropped(self):
        issue = self.compose_with([self.row(self.gig), self.row(self.other, stars=2.5)])
        self.assertEqual([p.opportunity for p in issue.picks], [self.gig])

    def test_without_ai_every_pick_goes_out_in_plain_words_to_the_reader(self):
        issue = compose.compose(self.ada, use_ai=False)
        self.assertEqual(len(issue.picks), 2)
        words = issue.picks[0].rationale
        self.assertTrue(words.startswith("About "))
        self.assertIn("Why it's here: shared interest in jazz", words)
        for desk_words in ("their", "Researched by AI", "We picked this"):
            self.assertNotIn(desk_words, words)

    def test_only_picks_above_the_floor_go_to_the_top(self):
        issue = self.compose_with([self.row(self.gig), self.row(self.other, stars=3)])
        self.assertEqual([p.opportunity for p in issue.top], [self.gig])

    def test_the_plan_keeps_only_days_things_are_really_on(self):
        today = timezone.localdate()
        on_day = today + timedelta(days=2)
        self.gig.start_date = on_day
        self.gig.save()
        ahead = event("Later gig", tags=[self.jazz], start_date=today + timedelta(days=20))
        wrong_day = today + timedelta(days=3)
        frame = self.frame(programme=[
            {"day": f"Monday {on_day.day}", "event_ids": [self.gig.pk], "plan": "The gig."},
            {"day": f"Tuesday {wrong_day.day}", "event_ids": [self.gig.pk], "plan": "The gig again."},
            {"day": "Any evening", "event_ids": [self.other.pk], "plan": "The other one."},
            {"day": "Friday 99", "event_ids": [self.other.pk], "plan": "Nowhere."},
            {"day": f"{wrong_day:%A}", "event_ids": [self.gig.pk], "plan": "By name, wrong day."},
            {"day": "Sunday", "event_ids": [], "plan": "A rest."},
            {"day": "Any evening", "event_ids": [ahead.pk], "plan": "Too soon."},
        ], section_notes=[{"section": "top", "note": "Two good ones."},
                          {"section": "film", "note": "No films in this issue."}],
           strongest=[999, self.other.pk, self.gig.pk, self.other.pk])
        issue = self.compose_with([self.row(self.gig), self.row(self.other), self.row(ahead)], frame,
                                  today=today)
        self.assertEqual(issue.programme, [
            {"day": f"{on_day:%A} {on_day.day}", "plan": "The gig."},
            {"day": "Any evening", "plan": "The other one."},
        ])
        self.assertEqual(issue.section_notes, {"top": "Two good ones."})
        self.assertEqual(issue.strongest, [self.other.pk, self.gig.pk])

    @override_settings(OPENAI_API_KEY="test-key")
    def test_picks_are_written_in_small_batches_and_skipped_ones_asked_for_again(self):
        config(max_per_section=10)
        for n in range(6):
            event(f"More jazz {n}", tags=[self.jazz])
        shy = self.other.pk
        asks, prompts = [], []

        def answer(system, user, schema, name, **kw):
            if name != "culture_week_picks":
                return None
            prompts.append(user)
            items = json.loads(user.split("as JSON:\n", 1)[1])
            ids = [i["event_id"] for i in items]
            asks.append(ids)
            skip = shy if len(asks) <= 2 else None
            return {"picks": [self.row(Opportunity(pk=i), stars=4, caveat="None.")
                              for i in ids if i != skip]}

        with mock.patch.object(ai, "_call", side_effect=answer):
            issue = compose.compose(self.ada)
        self.assertTrue(all(len(ids) <= ai.PICK_BATCH for ids in asks))
        self.assertEqual(asks[-1], [shy])
        self.assertEqual(len(issue.picks), 8)
        self.assertEqual({p.caveat for p in issue.picks}, {""})
        self.assertTrue(all("More jazz 5" in user for user in prompts))
        self.assertTrue(issue.written_by_ai)

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
        for part in ("The one I&#x27;d put at the top", "Filtered more tightly this week.",
                     "FOR YOU", "★★★★½", "Time Out ★★★★½", "Final week: closes",
                     "If I were programming your week", "My strongest bet this week",
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


GUARDIAN_BLOCK_PAGE = """<html><head><style>
.dcr-full{display:flex;background-color:var(--star-rating-background);}
.dcr-none{display:flex;background-color:var(--star-rating-empty-background);}
</style></head><body><h1>Ancient Infinity Orchestra review</h1>
<div><span class="dcr-full"></span><span class="dcr-full"></span><span class="dcr-full"></span>
<span class="dcr-full"></span><span class="dcr-none"></span></div>
""" + ("<p>filler</p>" * 400) + """<aside>63 Up review
<span class="dcr-full"></span><span class="dcr-full"></span><span class="dcr-none"></span>
<span class="dcr-none"></span><span class="dcr-none"></span></aside>
<script>{"starRating":2}</script></body></html>"""


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
        self.assertEqual(reviews.ratings_on_page("<p>4 out of 5 stars</p>"), ([], [4.0]))
        self.assertEqual(reviews.ratings_on_page("<p>3.5 out of 5</p>"), ([], [3.5]))
        self.assertEqual(reviews.ratings_on_page("<span>★★★☆☆</span>"), ([], [3.0]))
        self.assertEqual(reviews.ratings_on_page("<p>Rating: 3.5/5</p>"), ([], [3.5]))
        self.assertEqual(reviews.ratings_on_page("<p>on 2/5 we went</p>"), ([], []))

    def test_the_guardians_own_rating_block_not_a_card_for_another_review(self):
        page = GUARDIAN_BLOCK_PAGE
        self.assertEqual(reviews.ratings_on_page(page)[0], [4.0])
        r = reviews.check(self.review(stars=None, url="https://www.theguardian.com/tv/70-up-review"),
                          fetcher=lambda url: (200, page))
        self.assertEqual((r.verified, r.stars), (CriticReview.Verified.PAGE, Decimal("4")))

    def test_a_quote_has_to_be_on_the_page(self):
        page = ("<h1>Ancient Infinity Orchestra review</h1><p>It&#8217;s spiritual jazz \u2013 "
                "made <em>now</em>, not recreated.</p><p>4 out of 5 stars</p>")
        kept = reviews.check(self.review(quote="it’s spiritual jazz – made now…not recreated"),
                             fetcher=lambda url: (200, page))
        self.assertEqual(kept.quote, "it’s spiritual jazz – made now…not recreated")
        page_typo = "<h1>Ancient Infinity Orchestra</h1><p>corpse-in the-basement thriller</p><p>4 out of 5 stars</p>"
        tidied = reviews.check(self.review(quote="corpse‑in‑the‑basement thriller"),
                               fetcher=lambda url: (200, page_typo))
        self.assertEqual(tidied.quote, "corpse‑in‑the‑basement thriller")
        invented = reviews.check(self.review(quote="A triumph of the form"),
                                 fetcher=lambda url: (200, page))
        self.assertEqual(invented.quote, "")
        self.assertIn("isn't on the page", invented.check_note)
        self.assertEqual(invented.verified, CriticReview.Verified.PAGE)  # the stars still check out

    def test_radio_times_rating_block(self):
        page = ('<script>{"introduction":[[{"type":"editorial-ratings","data":{"starRatingValue":"4",'
                '"ratingValue":"4","isHalfStar":true}}]],"related":[{"type":"editorial-ratings",'
                '"data":{"starRatingValue":"2"}}]}</script>')
        self.assertEqual(reviews.ratings_on_page(page)[0], [4.0])

    def test_links_readers_can_open(self):
        self.assertEqual(reviews.public_url("https://tollbit.radiotimes.com/tv/drama/x-review/"),
                         "https://www.radiotimes.com/tv/drama/x-review/")
        self.assertEqual(reviews.public_url("https://nme.com/r?id=3&utm_source=openai"),
                         "https://nme.com/r?id=3")
        self.assertEqual(reviews.public_url("https://example.com/a"), "https://example.com/a")
        opp = event("Forever Home")
        saved = reviews.save_found(opp, [{"publication": "radio times", "stars": 4,
                                          "url": "https://tollbit.radiotimes.com/tv/drama/forever-home-review/"}],
                                   fetcher=lambda url: (200, "<h1>Forever Home review</h1><p>A star rating of 4 out of 5.</p>"))
        self.assertEqual((saved[0].publication, saved[0].url, saved[0].verified),
                         ("Radio Times", "https://www.radiotimes.com/tv/drama/forever-home-review/",
                          CriticReview.Verified.PAGE))

    def test_rating_text_only_confirms_what_ai_already_said(self):
        page = "<h1>Ancient Infinity Orchestra</h1><p>3.5 out of 5</p><p>4 out of 5</p>"
        confirmed = reviews.check(self.review(stars=Decimal("3.5")), fetcher=lambda url: (200, page))
        self.assertEqual(confirmed.verified, CriticReview.Verified.PAGE)
        unsure = reviews.check(self.review(stars=None), fetcher=lambda url: (200, page))
        self.assertEqual(unsure.verified, "")
        self.assertIn("check it's this review's rating", unsure.check_note)

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


class ResearchLinkTests(TestCase):
    """A reader should land on the page for the thing, and it should open."""

    def row(self, **kw):
        data = {"title": "70 Up", "description": "The last one.", "category": "watch",
                "price_tier": "free", "price_display": "", "location_name": "ITV1",
                "location_area": "UK", "booking_url": "https://www.itv.com/",
                "start_date": timezone.localdate().isoformat(), "end_date": "",
                "mainstream_to_unusual": 2, "intimate_to_large_scale": 3, "tags": [],
                "sources": [{"title": "ITVX", "url": "https://www.itv.com/watch/70-up/abc"}]}
        data.update(kw)
        return {"listings": [data]}

    def test_a_front_page_link_gives_way_to_the_page_for_the_item(self):
        created = research.save_drafts(self.row())
        self.assertEqual(created[0].booking_url, "https://www.itv.com/watch/70-up/abc")

    def test_a_front_page_is_kept_rather_than_swapped_for_a_forum_thread(self):
        created = research.save_drafts(self.row(sources=[
            {"title": "r/television", "url": "https://www.reddit.com/r/television/comments/1/70_up/"},
            {"title": "Schedules", "url": "https://www.tvzoneuk.com/post/dates"}]))
        self.assertEqual(created[0].booking_url, "https://www.itv.com/")

    def test_a_dead_link_is_not_replaced_by_a_forum_thread(self):
        payload = self.row(booking_url="https://www.itv.com/gone", sources=[
            {"title": "r/television", "url": "https://www.reddit.com/r/television/comments/1/"}])
        with mock.patch.object(research, "link_status",
                               side_effect=lambda url: 404 if url.endswith("gone") else 200):
            self.assertEqual(research.save_drafts(payload, check_links=True), [])

    def test_a_site_search_is_not_the_page_for_the_item(self):
        self.assertTrue(research._is_homepage("https://designmuseum.org/search?page=1&q=past"))
        self.assertTrue(research._is_homepage("https://www.itv.com/"))
        self.assertFalse(research._is_homepage("https://www.itv.com/watch/70-up/abc"))
        self.assertFalse(research._is_homepage(
            "https://events.nationaltheatre.org.uk/events/95366?promo=16225YO"))

    def test_the_same_thing_named_twice_is_saved_once(self):
        research.save_drafts(self.row(title="Ahmedabad International Film Festival",
                                      booking_url="https://example.com/aiff"))
        again = research.save_drafts(self.row(title="Ahmedabad International Film Festival 2026",
                                              booking_url="https://example.com/aiff-2026"))
        self.assertEqual(again, [])
        other = research.save_drafts(self.row(title="Ahmedabad International Children Film Festival",
                                              booking_url="https://example.com/kids"))
        self.assertEqual(len(other), 1)   # a different festival, however alike the name

    def test_no_venue_is_left_blank_not_a_dash(self):
        created = research.save_drafts(self.row(location_name="—"))
        self.assertEqual(created[0].location_name, "")

    def test_links_come_without_ai_gateways_or_tracking(self):
        created = research.save_drafts(self.row(
            booking_url="https://www.itv.com/watch/70-up/abc?utm_source=openai"))
        self.assertEqual(created[0].booking_url, "https://www.itv.com/watch/70-up/abc")

    def test_something_already_over_is_not_saved(self):
        past = (timezone.localdate() - timedelta(days=5)).isoformat()
        self.assertEqual(research.save_drafts(self.row(category="music", start_date=past,
                                                       end_date=past)), [])

    def test_a_dead_link_is_replaced_by_a_working_source_or_the_find_is_dropped(self):
        payload = self.row(booking_url="https://www.itv.com/gone",
                           sources=[{"title": "a", "url": "https://www.itv.com/watch/70-up/abc"}])
        with mock.patch.object(research, "link_status",
                               side_effect=lambda url: 404 if url.endswith("gone") else 200):
            created = research.save_drafts(payload, check_links=True)
        self.assertEqual(created[0].booking_url, "https://www.itv.com/watch/70-up/abc")
        self.assertIn("dead", created[0].editorial_note)
        Opportunity.objects.all().delete()
        with mock.patch.object(research, "link_status", return_value=404):
            self.assertEqual(research.save_drafts(payload, check_links=True), [])

    def test_a_link_we_could_not_open_is_kept_with_a_note(self):
        with mock.patch.object(research, "link_status", return_value=403):
            created = research.save_drafts(self.row(booking_url="https://www.itv.com/x"),
                                           check_links=True)
        self.assertIn("didn't open", created[0].editorial_note)


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
