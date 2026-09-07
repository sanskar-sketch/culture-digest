from django.db import migrations, models


class Migration(migrations.Migration):
    """Rename rather than drop-and-add.

    Django's autodetector proposed removing `resend_message_id` and adding
    `provider_message_id`, which would discard every stored message id.
    RenameField preserves the column contents - the ids are how a specific
    send gets traced with the email provider after the fact.
    """

    dependencies = [
        ("recommendations", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="newsletterissue",
            old_name="resend_message_id",
            new_name="provider_message_id",
        ),
        migrations.AlterField(
            model_name="newsletterissue",
            name="provider_message_id",
            field=models.CharField(
                blank=True,
                help_text="Message id returned by the email provider, for tracing a send.",
                max_length=120,
            ),
        ),
    ]
