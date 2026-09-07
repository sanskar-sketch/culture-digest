"""A guard against fields nobody can reach.

Adding a field to a model does not put it in the admin: every ModelAdmin
here declares explicit fieldsets, so a new field is simply absent from the
form and nothing says so. That has already happened once - `critic_quote`
existed for a deploy without any way for an editor to fill it in, which
meant the feature it was added for silently did nothing.

This asserts every editable field is reachable somewhere: in the fieldsets,
in readonly_fields, in filter_horizontal, or explicitly excluded. Excluding
is fine - it just has to be a decision someone wrote down, rather than an
omission.
"""

from django.contrib import admin as django_admin
from django.test import TestCase

# Fields deliberately kept off a form, with the reason.
INTENTIONALLY_ABSENT = {
    # label: {field: why}
}


def reachable_fields(model_admin) -> set | None:
    """What an editor can see or that was explicitly excluded.

    None means the admin declares no explicit field list, so Django renders
    everything and there is nothing to check.
    """
    found = set()
    fieldsets = getattr(model_admin, "fieldsets", None)
    if fieldsets:
        for _, options in fieldsets:
            for field in options.get("fields", ()):
                if isinstance(field, (list, tuple)):
                    found.update(field)
                else:
                    found.add(field)
    elif getattr(model_admin, "fields", None):
        found.update(model_admin.fields)
    else:
        return None

    found.update(getattr(model_admin, "readonly_fields", ()) or ())
    found.update(getattr(model_admin, "exclude", ()) or ())
    found.update(getattr(model_admin, "filter_horizontal", ()) or ())
    return found


def editable_fields(model) -> set:
    return {
        field.name
        for field in model._meta.get_fields()
        if getattr(field, "editable", False) and not field.auto_created
    }


class AdminCoverageTests(TestCase):
    def test_every_editable_field_is_reachable_in_the_admin(self):
        gaps = {}
        for model, model_admin in django_admin.site._registry.items():
            reachable = reachable_fields(model_admin)
            if reachable is None:
                continue
            label = model._meta.label
            allowed = set(INTENTIONALLY_ABSENT.get(label, {}))
            missing = editable_fields(model) - reachable - allowed
            if missing:
                gaps[label] = sorted(missing)

        self.assertEqual(
            gaps, {},
            "These model fields exist but no editor can reach them. Add them to the "
            "admin's fieldsets, or to INTENTIONALLY_ABSENT with a reason:\n"
            + "\n".join(f"  {label}: {fields}" for label, fields in gaps.items()),
        )

    def test_the_guard_actually_detects_a_missing_field(self):
        """The check above only earns its place if it can fail."""
        from opportunities.admin import OpportunityAdmin
        from opportunities.models import Opportunity

        stripped = [
            (name, {**opts, "fields": tuple(
                f for f in opts["fields"] if f != "critic_quote")})
            for name, opts in OpportunityAdmin.fieldsets
        ]
        original = OpportunityAdmin.fieldsets
        try:
            OpportunityAdmin.fieldsets = stripped
            admin_instance = django_admin.site._registry[Opportunity]
            self.assertNotIn("critic_quote", reachable_fields(admin_instance))
        finally:
            OpportunityAdmin.fieldsets = original
