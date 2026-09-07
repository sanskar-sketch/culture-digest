import uuid

from django.db import migrations, models


def give_each_reader_a_token(apps, schema_editor):
    """AddField applies one default to every existing row, so they would all
    share a token - and one reader's link would open another's profile.
    Assign a fresh one per row before the unique constraint goes on."""
    Reader = apps.get_model("readers", "Reader")
    for reader in Reader.objects.all().only("id"):
        Reader.objects.filter(pk=reader.pk).update(edit_token=uuid.uuid4())


class Migration(migrations.Migration):

    dependencies = [
        ("readers", "0006_reader_other_availability_reader_other_budget_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="reader",
            name="edit_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
        migrations.RunPython(give_each_reader_a_token, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="reader",
            name="edit_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
