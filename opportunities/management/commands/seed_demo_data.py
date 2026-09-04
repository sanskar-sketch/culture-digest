from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from opportunities.models import Opportunity, Tag
from readers.models import Reader

TAGS = [
    "jazz", "classical music", "theatre", "immersive", "contemporary art",
    "photography", "spoken word", "comedy", "fine dining", "street food",
    "film", "documentary", "history", "science", "design",
]

OPPORTUNITIES = [
    dict(
        title="Late-night jazz at The Vortex",
        category=Opportunity.Category.MUSIC,
        description="An intimate late set from a rising quartet in a room that seats sixty.",
        editorial_note="Perfect for someone who likes their music close-up and a little unpredictable.",
        tags=["jazz"],
        price_tier=Opportunity.PriceTier.BUDGET,
        price_display="£15",
        location_name="The Vortex",
        location_area="London",
        booking_url="https://example.com/book/jazz-vortex",
        mainstream_to_unusual=3,
        intimate_to_large_scale=1,
        critic_rating=4.2,
        critic_rating_source="Time Out, 4/5",
    ),
    dict(
        title="Immersive Alice in a disused warehouse",
        category=Opportunity.Category.UNUSUAL,
        description="Wander through six rooms of Wonderland built inside a Hackney warehouse.",
        editorial_note="For readers who said they loved immersive theatre or want to try it.",
        tags=["theatre", "immersive"],
        price_tier=Opportunity.PriceTier.PREMIUM,
        price_display="£65",
        location_name="Unit 4, Hackney Wick",
        location_area="London",
        booking_url="https://example.com/book/immersive-alice",
        mainstream_to_unusual=5,
        intimate_to_large_scale=2,
        critic_rating=4.6,
        critic_rating_source="The Guardian, 5/5",
    ),
    dict(
        title="Free photography retrospective at the Barbican",
        category=Opportunity.Category.EXHIBITION,
        description="A career-spanning look at a mid-century street photographer, free entry.",
        tags=["photography", "contemporary art"],
        price_tier=Opportunity.PriceTier.FREE,
        price_display="Free",
        location_name="Barbican Centre",
        location_area="London",
        booking_url="https://example.com/book/barbican-photo",
        mainstream_to_unusual=2,
        intimate_to_large_scale=4,
        critic_rating=4.0,
        critic_rating_source="FT, 4/5",
    ),
    dict(
        title="Tasting menu at a 12-seat counter",
        category=Opportunity.Category.FOOD,
        description="A seven-course tasting menu served at a counter with an open kitchen.",
        editorial_note="A splurge, but the kind of night that becomes a favourite story.",
        tags=["fine dining"],
        price_tier=Opportunity.PriceTier.SPLURGE,
        price_display="£140pp",
        location_name="Counter Thirteen",
        location_area="London",
        booking_url="https://example.com/book/counter-thirteen",
        mainstream_to_unusual=3,
        intimate_to_large_scale=1,
        critic_rating=4.8,
        critic_rating_source="Eater, 5/5",
    ),
    dict(
        title="Sunday street food market",
        category=Opportunity.Category.FOOD,
        description="Forty stalls, a sunny courtyard, and no booking required.",
        tags=["street food"],
        price_tier=Opportunity.PriceTier.BUDGET,
        price_display="£5-£10 a dish",
        location_name="Maltby Street",
        location_area="London",
        booking_url="https://example.com/book/maltby-market",
        mainstream_to_unusual=2,
        intimate_to_large_scale=4,
        critic_rating=None,
    ),
    dict(
        title="Q&A screening of a new science documentary",
        category=Opportunity.Category.FILM,
        description="A one-off screening followed by a Q&A with the director.",
        tags=["film", "documentary", "science"],
        price_tier=Opportunity.PriceTier.MODERATE,
        price_display="£22",
        location_name="Barbican Cinema",
        location_area="London",
        booking_url="https://example.com/book/science-doc",
        mainstream_to_unusual=3,
        intimate_to_large_scale=3,
        critic_rating=3.9,
        critic_rating_source="Sight & Sound, 4/5",
    ),
    dict(
        title="Stand-up night above a pub",
        category=Opportunity.Category.EVENT,
        description="Six new acts trying out material in a sixty-seat room above a pub.",
        tags=["comedy"],
        price_tier=Opportunity.PriceTier.BUDGET,
        price_display="£10",
        location_name="The Camden Head",
        location_area="London",
        booking_url="https://example.com/book/pub-comedy",
        mainstream_to_unusual=4,
        intimate_to_large_scale=1,
        critic_rating=None,
    ),
    dict(
        title="Design Museum lates: talks and tours",
        category=Opportunity.Category.TALK,
        description="After-hours access plus three short talks from working designers.",
        tags=["design"],
        price_tier=Opportunity.PriceTier.MODERATE,
        price_display="£18",
        location_name="Design Museum",
        location_area="London",
        booking_url="https://example.com/book/design-museum-lates",
        mainstream_to_unusual=3,
        intimate_to_large_scale=3,
        critic_rating=None,
    ),
]


class Command(BaseCommand):
    help = "Seed the database with sample tags, opportunities, and one demo reader, for local development."

    def handle(self, *args, **options):
        tag_objs = {}
        for name in TAGS:
            tag, _ = Tag.objects.get_or_create(name=name)
            tag_objs[name] = tag

        created = 0
        for data in OPPORTUNITIES:
            tag_names = data.pop("tags")
            data["status"] = Opportunity.Status.PUBLISHED
            data["start_date"] = timezone.localdate()
            data["end_date"] = timezone.localdate() + timedelta(days=60)
            opp, was_created = Opportunity.objects.update_or_create(
                title=data["title"], defaults=data
            )
            opp.tags.set([tag_objs[n] for n in tag_names])
            created += int(was_created)

        reader, reader_created = Reader.objects.update_or_create(
            email="demo.reader@example.com",
            defaults=dict(
                name="Demo Reader",
                location="London",
                travel_radius=Reader.TravelRadius.WITHIN_CITY,
                budget=Reader.Budget.MODERATE,
                availability=[Reader.Availability.WEEKENDS, Reader.Availability.WEEKDAY_EVENINGS],
                mainstream_preference=4,
                scale_preference=2,
                loved_examples="A tiny immersive show in an old warehouse; a jazz set in a basement.",
                disliked_examples="Big arena gigs, anything with audience participation on stage.",
            ),
        )
        reader.interest_tags.set(
            [tag_objs[n] for n in ["jazz", "immersive", "theatre", "photography"]]
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(TAGS)} tags, {len(OPPORTUNITIES)} opportunities "
                f"({created} newly created), and demo reader {reader.email}."
            )
        )
