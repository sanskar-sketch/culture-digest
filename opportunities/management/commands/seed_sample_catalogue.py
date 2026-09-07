"""Seed a starter catalogue of sample opportunities.

Everything created here is a DRAFT, on purpose. Drafts are never matched or
sent (the engine only considers published entries), so an editor reviews,
corrects the details and publishes deliberately. That matters because these
are invented listings with placeholder booking links - if they went out as
real recommendations, a reader would be sent somewhere that doesn't exist.

Venue names are invented rather than borrowed from real places, for the same
reason: a fake event attached to a real venue's name is a false record about
that venue.

Run it with:  python manage.py seed_sample_catalogue
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from opportunities.models import Category, Opportunity, Tag

PLACEHOLDER_URL = "https://example.com/replace-with-the-real-booking-link"
SAMPLE_MARKER = "SAMPLE DATA - check every detail and replace the booking link before publishing."

# title, category, price_tier, price_display, venue, area, mainstream(1-5),
# scale(1-5), critic_rating, tags, description
SAMPLES = [
    # Theatre
    ("A Chekhov revival in a ninety-seat room", Category.THEATRE, "moderate", "£22",
     "The Lamplight Room", "London", 3, 1, 4.5, ["plays"],
     "A pared-back revival of a Russian classic, staged in the round for an audience of ninety."),
    ("Late-night musical revue in a converted garage", Category.THEATRE, "budget", "£14",
     "Garage 27", "Manchester", 4, 2, None, ["musicals", "fringe & experimental"],
     "A loose, funny late-night revue that changes its running order every week."),
    ("Promenade piece through three floors of an old bank", Category.THEATRE, "premium", "£55",
     "The Old Counting House", "London", 5, 2, 4.0, ["immersive theatre"],
     "The audience moves between rooms in small groups; no two routes are the same."),
    ("New dance work for six performers", Category.THEATRE, "moderate", "£25",
     "Fold Studios", "Bristol", 4, 2, 4.2, ["dance"],
     "A sixty-minute piece for six dancers, built around live percussion."),

    # Music
    ("Trio residency in a basement jazz room", Category.MUSIC, "budget", "£16",
     "The Coal Hole", "London", 4, 1, 4.4, ["jazz"],
     "A piano trio playing a month-long Tuesday residency to about sixty people."),
    ("String quartet by candlelight", Category.MUSIC, "moderate", "£28",
     "St Anselm's Hall", "Edinburgh", 2, 2, 4.1, ["classical music"],
     "An early-evening programme of Debussy and Ravel in a stone hall."),
    ("Modular synth night in a railway arch", Category.MUSIC, "budget", "£12",
     "Arch Nine", "London", 5, 2, None, ["electronic"],
     "Four artists performing on modular rigs, standing room, doors at nine."),
    ("Touring folk act on their last UK date", Category.MUSIC, "moderate", "£24",
     "The Weaving Shed", "Leeds", 3, 3, 4.3, ["folk & world"],
     "A five-piece closing a UK tour in a former textile mill."),
    ("Chamber opera in a single act", Category.MUSIC, "premium", "£48",
     "Northgate Playhouse", "London", 3, 2, 4.6, ["opera", "classical music"],
     "A seventy-minute chamber opera for four singers and a small ensemble."),

    # Film
    ("Restored print of a 1960s road movie", Category.FILM, "budget", "£13",
     "The Ritzy Picture House", "London", 3, 2, 4.5, ["classics & repertory"],
     "A new 4K restoration, introduced by the archivist who oversaw it."),
    ("Documentary premiere with director Q&A", Category.FILM, "moderate", "£20",
     "Riverside Screen", "London", 3, 2, 4.2, ["documentary"],
     "First UK screening of a documentary about coastal erosion, followed by a Q&A."),
    ("Subtitled double bill, two countries, one theme", Category.FILM, "budget", "£18",
     "The Bower Cinema", "Glasgow", 4, 2, None, ["arthouse & foreign"],
     "Two features shown back to back with a half-hour break between them."),
    ("Opening-weekend screening of a wide release", Category.FILM, "budget", "£15",
     "Central Screens", "London", 1, 3, 3.8, ["new releases"],
     "A mainstream release on its opening weekend, in the largest screen in the building."),

    # Exhibition
    ("Retrospective of a mid-century street photographer", Category.EXHIBITION, "free", "Free",
     "The Print Rooms", "London", 3, 2, 4.4, ["photography"],
     "Around a hundred and forty prints, most never shown in the UK before."),
    ("Contemporary painting from a single studio year", Category.EXHIBITION, "budget", "£11",
     "Bracken Gallery", "London", 4, 1, 4.0, ["contemporary art"],
     "Everything one painter made in twelve months, hung chronologically."),
    ("Industrial design from the postwar decades", Category.EXHIBITION, "moderate", "£19",
     "The Makers' Institute", "Birmingham", 2, 3, 4.1, ["design"],
     "Furniture, tools and packaging from 1945 to 1975, with the prototypes alongside."),
    ("Local history in objects, one street at a time", Category.EXHIBITION, "free", "Free",
     "Parish Museum", "York", 2, 1, None, ["history & museums"],
     "A small, carefully-labelled show about a single street over four centuries."),

    # Talks
    ("Astrophysicist on what we still cannot see", Category.TALK, "budget", "£12",
     "The Lecture Hall", "London", 2, 3, 4.3, ["science"],
     "An hour on dark matter for a general audience, with time for questions."),
    ("Two historians disagree in public", Category.TALK, "budget", "£15",
     "Assembly Rooms", "Edinburgh", 3, 2, 4.5, ["history talks", "politics & current affairs"],
     "A chaired disagreement between two historians about the same decade."),
    ("Novelist in conversation about a first book", Category.TALK, "budget", "£10",
     "The Reading Room", "London", 3, 1, None, ["literature & books"],
     "A debut novelist talks about the six years it took to finish."),
    ("Philosophy for a Sunday afternoon", Category.TALK, "free", "Free",
     "Community Library", "Bristol", 4, 1, None, ["philosophy & ideas"],
     "An informal monthly discussion group; no reading required beforehand."),

    # Food
    ("Twelve-seat counter, one sitting a night", Category.FOOD, "splurge", "£120",
     "Counter & Co", "London", 4, 1, 4.7, ["fine dining"],
     "A single nightly sitting at a twelve-seat counter; the menu changes weekly."),
    ("Regional street food from one province", Category.FOOD, "budget", "£12",
     "Corner Market Stalls", "London", 3, 2, 4.2, ["street food"],
     "A stall cooking from one province only, with about eight dishes."),
    ("Natural wine tasting with the importer", Category.FOOD, "moderate", "£35",
     "The Bottle Shop", "Manchester", 4, 1, None, ["wine & cocktails"],
     "Eight wines poured and explained by the person who brings them in."),
    ("Supper club in a working greenhouse", Category.FOOD, "premium", "£58",
     "Glasshouse Nine", "London", 5, 2, 4.3, ["supper clubs"],
     "A long-table dinner for thirty among the plants, once a month."),

    # Events
    ("Stand-up work-in-progress, four acts", Category.EVENT, "budget", "£9",
     "Upstairs at The Bell", "London", 3, 1, None, ["comedy"],
     "Four comedians trying material that is not finished yet."),
    ("Two-day festival across four venues", Category.EVENT, "premium", "£65",
     "Various, city centre", "Bristol", 3, 5, 4.1, ["festivals"],
     "A weekend pass covering four venues within ten minutes' walk."),
    ("Monthly record fair", Category.EVENT, "free", "Free entry",
     "The Corn Exchange", "Leeds", 2, 3, None, ["markets & fairs"],
     "Around forty sellers; early entry an hour before general admission."),
    ("Two-hour introduction to letterpress", Category.EVENT, "moderate", "£40",
     "The Type Workshop", "London", 4, 1, None, ["workshops & classes"],
     "A hands-on session; everyone leaves with something they set and printed."),

    # Unusual
    ("Guided walk through a decommissioned tunnel", Category.UNUSUAL, "moderate", "£30",
     "Disused rail tunnel, meeting point provided", "London", 5, 2, 4.4,
     ["secret & underground", "outdoor & adventure"],
     "A ninety-minute walk underground in small groups, with sound installations."),
    ("Museum open until midnight, once a season", Category.UNUSUAL, "budget", "£16",
     "City Collections", "London", 4, 4, 4.0, ["late-night openings"],
     "The permanent collection after dark, with fewer people and different lighting."),
    ("Dinner in complete darkness", Category.UNUSUAL, "premium", "£70",
     "The Blackout Room", "London", 5, 1, 3.9, ["immersive experiences"],
     "Three courses served in a room with no light at all."),
    ("Dawn swim and breakfast", Category.UNUSUAL, "budget", "£18",
     "The Lido", "Brighton", 4, 2, None, ["outdoor & adventure"],
     "An organised early swim followed by breakfast; wetsuits optional."),

    # Online, so it matches readers anywhere
    ("Streamed lecture series on modernist architecture", Category.TALK, "budget", "£8",
     "", "Online", 3, 3, None, ["history talks", "design"],
     "A four-part series, watchable live or for a fortnight afterwards."),
]


class Command(BaseCommand):
    help = (
        "Create a starter catalogue of sample opportunities as drafts, for an "
        "editor to review, correct and publish."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--publish",
            action="store_true",
            help="Publish immediately instead of leaving them as drafts. Only do "
                 "this on a demo environment - the booking links are placeholders.",
        )

    def handle(self, *args, **options):
        status = (
            Opportunity.Status.PUBLISHED if options["publish"]
            else Opportunity.Status.DRAFT
        )
        today = timezone.localdate()

        # Bulk, not one-at-a-time. The database can be a long way from the
        # app server (ours is a different continent), and 34 listings done
        # row by row is well over a hundred round trips - enough to outlast
        # a web request when this is triggered from the admin button.
        existing = set(Opportunity.objects.values_list("slug", flat=True))
        tag_ids = dict(Tag.objects.values_list("name", "id"))

        to_create, wanted_tags = [], {}
        for (title, category, price_tier, price_display, venue, area,
             mainstream, scale, rating, tag_names, description) in SAMPLES:
            slug = slugify(title)[:220]
            if slug in existing:
                continue
            to_create.append(Opportunity(
                title=title, slug=slug, category=category, description=description,
                editorial_note=SAMPLE_MARKER, price_tier=price_tier,
                price_display=price_display, location_name=venue, location_area=area,
                is_online=(area.lower() == "online"), booking_url=PLACEHOLDER_URL,
                start_date=today, end_date=today + timedelta(days=90),
                critic_rating=rating,
                critic_rating_source="Sample rating" if rating else "",
                mainstream_to_unusual=mainstream, intimate_to_large_scale=scale,
                status=status,
            ))
            wanted_tags[slug] = tag_names
            missing = set(tag_names) - set(tag_ids)
            if missing:
                self.stdout.write(self.style.WARNING(
                    f"  {title}: no such tag(s) {sorted(missing)}"))

        skipped = len(SAMPLES) - len(to_create)
        Opportunity.objects.bulk_create(to_create)
        created = len(to_create)

        if created:
            ids = dict(
                Opportunity.objects.filter(slug__in=wanted_tags)
                .values_list("slug", "id")
            )
            through = Opportunity.tags.through
            through.objects.bulk_create([
                through(opportunity_id=ids[slug], tag_id=tag_ids[name])
                for slug, names in wanted_tags.items()
                for name in names
                if name in tag_ids and slug in ids
            ], ignore_conflicts=True)

        self.stdout.write(self.style.SUCCESS(
            f"Created {created} sample opportunit{'y' if created == 1 else 'ies'}"
            f" as {status}." + (f" Skipped {skipped} that already existed." if skipped else "")
        ))
        if status == Opportunity.Status.DRAFT:
            self.stdout.write(
                "They are drafts, so nothing will be sent. Review them in the admin, "
                "replace the placeholder booking links, then use the 'Publish selected' "
                "action."
            )
