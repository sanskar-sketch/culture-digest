"""Sport gets its own place at signup, and specific sports to pick from.

Until now "sport" was one interest filed under Event, so someone who cared
about cricket and nothing else had no way to say so. The old interest
moves under Sport - it keeps its slug, so anyone who picked it keeps it -
and is named for what it now means next to the specific ones.
"""

from django.db import migrations
from django.utils.text import slugify

SPORTS = [
    "football", "cricket", "rugby", "tennis", "athletics", "motorsport",
    "boxing & martial arts", "golf", "cycling", "running & marathons",
    "basketball", "horse racing",
]


def seed(apps, schema_editor):
    Tag = apps.get_model("opportunities", "Tag")
    Tag.objects.filter(slug="sport").update(category="sport", name="any live sport")
    for name in SPORTS:
        slug = slugify(name)
        existing = Tag.objects.filter(slug=slug).first() or Tag.objects.filter(name=name).first()
        if existing is None:
            Tag.objects.create(name=name, slug=slug, category="sport")
        elif not existing.category or existing.category == "event":
            existing.category = "sport"
            existing.save(update_fields=["category"])


def unseed(apps, schema_editor):
    Tag = apps.get_model("opportunities", "Tag")
    Tag.objects.filter(slug="sport").update(category="event", name="sport")


class Migration(migrations.Migration):

    dependencies = [
        ("opportunities", "0011_sport_category"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
