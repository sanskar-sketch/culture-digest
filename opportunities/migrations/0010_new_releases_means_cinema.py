from django.db import migrations


def rename(apps, schema_editor):
    """'New releases' sat under Film and meant new films - but research read
    it as any new thing and hung it on novels, albums and boxsets, which then
    outscored everything else in the week."""
    Tag = apps.get_model("opportunities", "Tag")
    Tag.objects.filter(slug="new-releases", name="new releases").update(name="new in cinemas")


def back(apps, schema_editor):
    Tag = apps.get_model("opportunities", "Tag")
    Tag.objects.filter(slug="new-releases", name="new in cinemas").update(name="new releases")


class Migration(migrations.Migration):
    dependencies = [("opportunities", "0009_seed_release_interests")]
    operations = [migrations.RunPython(rename, back)]
