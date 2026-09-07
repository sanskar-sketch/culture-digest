from django.db import migrations
from django.utils.text import slugify

# The interest taxonomy offered during onboarding, grouped under the
# categories named in the brief (theatre, music, film, exhibitions, talks,
# food, events, unusual experiences). Editors can add/rename freely in the
# admin afterwards - this only guarantees a sensible starting set exists,
# since an empty tag table leaves the onboarding step with nothing to pick.
TAXONOMY = {
    "theatre": [
        "plays", "musicals", "immersive theatre", "fringe & experimental", "dance",
    ],
    "music": [
        "jazz", "classical music", "live gigs", "electronic", "opera", "folk & world",
    ],
    "film": [
        "new releases", "arthouse & foreign", "documentary", "classics & repertory",
    ],
    "exhibition": [
        "contemporary art", "photography", "design", "history & museums", "fashion",
    ],
    "talk": [
        "science", "politics & current affairs", "literature & books",
        "history talks", "philosophy & ideas",
    ],
    "food": [
        "fine dining", "street food", "wine & cocktails", "supper clubs", "food markets",
    ],
    "event": [
        "comedy", "festivals", "markets & fairs", "workshops & classes", "sport",
    ],
    "unusual": [
        "immersive experiences", "secret & underground", "late-night openings",
        "outdoor & adventure",
    ],
}

# Tags that already existed before categories were introduced, mapped onto
# the new taxonomy so nothing gets orphaned or duplicated.
LEGACY_RENAMES = {
    "film": ("new releases", "film"),
    "theatre": ("plays", "theatre"),
    "immersive": ("immersive experiences", "unusual"),
    "spoken word": ("literature & books", "talk"),
    "history": ("history & museums", "exhibition"),
}


def seed_taxonomy(apps, schema_editor):
    Tag = apps.get_model("opportunities", "Tag")

    # Give any pre-existing tag a category where we can infer one, so the
    # relevance filter doesn't silently hide them.
    existing_by_name = {t.name.lower(): t for t in Tag.objects.all()}
    for category, names in TAXONOMY.items():
        for name in names:
            tag = existing_by_name.get(name.lower())
            if tag is not None:
                if not tag.category:
                    tag.category = category
                    tag.save(update_fields=["category"])
                continue
            Tag.objects.create(name=name, slug=slugify(name)[:70], category=category)

    # Older tags that don't appear in the taxonomy above still need a home,
    # otherwise they'd only ever show under "no categories picked".
    for tag in Tag.objects.filter(category=""):
        _, category = LEGACY_RENAMES.get(tag.name.lower(), (None, ""))
        if category:
            tag.category = category
            tag.save(update_fields=["category"])


def unseed(apps, schema_editor):
    """Only remove tags nothing is using - never delete an editor's work."""
    Tag = apps.get_model("opportunities", "Tag")
    seeded = [name for names in TAXONOMY.values() for name in names]
    (
        Tag.objects.filter(name__in=seeded, opportunities__isnull=True,
                           interested_readers__isnull=True)
        .distinct()
        .delete()
    )


class Migration(migrations.Migration):

    dependencies = [
        ("opportunities", "0002_alter_tag_options_tag_category"),
    ]

    operations = [
        migrations.RunPython(seed_taxonomy, unseed),
    ]
