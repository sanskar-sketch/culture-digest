"""Move a live site onto the culture-week shape.

The subject line only changes if it is still the shipped one - an editor's
own wording is left alone. The per-issue cap rises to the new ceiling,
because the old one described a four-pick email that no longer exists.
"""

from django.db import migrations

OLD_SUBJECT = "{name}{count} thing{plural} worth your time"
NEW_SUBJECT = "{possessive}culture week: {week}"


def forwards(apps, schema_editor):
    SiteConfig = apps.get_model("siteconfig", "SiteConfig")
    for config in SiteConfig.objects.all():
        changed = []
        if config.subject_template == OLD_SUBJECT:
            config.subject_template = NEW_SUBJECT
            changed.append("subject_template")
        if config.recommendations_per_send < 20:
            config.recommendations_per_send = 20
            changed.append("recommendations_per_send")
        if changed:
            config.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ("siteconfig", "0009_siteconfig_ai_issue_timeout_seconds_and_more"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
