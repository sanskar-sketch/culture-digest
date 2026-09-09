"""Give every campaign delivery its own token.

Written by hand rather than left as Django generated it. A single
AddField with `default=uuid.uuid4, unique=True` evaluates the default
once for the backfill, so every existing row gets the *same* UUID and the
unique constraint is violated the moment it is applied - on an empty
table that passes, and on production it does not.

So: add it nullable, fill it in a row at a time, then tighten.
"""

import uuid

from django.db import migrations, models


def give_each_one(apps, schema_editor):
    CampaignDelivery = apps.get_model("campaigns", "CampaignDelivery")
    for delivery in CampaignDelivery.objects.filter(token__isnull=True).iterator():
        CampaignDelivery.objects.filter(pk=delivery.pk).update(token=uuid.uuid4())


def noop(apps, schema_editor):
    """Reversing only drops the column, which the AddField undoes."""


class Migration(migrations.Migration):

    dependencies = [
        ("campaigns", "0003_campaign_ends_on_campaign_frequency_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaigndelivery",
            name="token",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(give_each_one, noop),
        migrations.AlterField(
            model_name="campaigndelivery",
            name="token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
