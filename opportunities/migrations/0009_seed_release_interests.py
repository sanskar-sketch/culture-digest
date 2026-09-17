"""Interests for the things you don't go to: books, new music, TV.

Genre interests like jazz or contemporary art already cross over - an album
tagged jazz reaches jazz readers as a gig does. These are the ones that
only make sense for releases, so a reader who picks Books or New music at
signup has something specific to choose.
"""

from django.db import migrations
from django.utils.text import slugify

RELEASE_INTERESTS = {
    "book": ["fiction", "history & biography", "memoir", "politics & ideas books"],
    "listen": ["new albums", "singer-songwriters"],
    "watch": ["tv drama", "documentary series", "comedy series"],
}


def seed(apps, schema_editor):
    Tag = apps.get_model("opportunities", "Tag")
    for category, names in RELEASE_INTERESTS.items():
        for name in names:
            slug = slugify(name)
            if not Tag.objects.filter(slug=slug).exists() and not Tag.objects.filter(name=name).exists():
                Tag.objects.create(name=name, slug=slug, category=category)


class Migration(migrations.Migration):

    dependencies = [
        ("opportunities", "0008_alter_opportunity_category_alter_tag_category_and_more"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
