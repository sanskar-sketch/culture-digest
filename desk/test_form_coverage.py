"""A guard against fields nobody can reach, for the desk's own forms.

This is the desk's version of config/test_admin_coverage.py's guard,
which used to check Django admin's ModelAdmin.fieldsets. Now that the
desk's plain ModelForms are where an editor actually reaches a field,
the same incident (critic_quote existing for a deploy with no way to
fill it in) can happen here just as easily - a field left out of a
form's Meta.fields is invisible, and nothing else says so.
"""

from django.test import TestCase

from campaigns.models import Campaign
from desk.forms import CampaignForm, EmailTemplateForm, OpportunityForm, ReaderForm, TagForm
from opportunities.models import Opportunity, Tag
from readers.models import Reader
from siteconfig.emails import EmailTemplate

# Fields deliberately kept off a desk form, with the reason.
INTENTIONALLY_ABSENT = {
    "campaigns.Campaign": {
        # Set by the view (created_by) or by the send/schedule actions
        # (status, started_at, finished_at, last_error), never hand-edited.
        "created_by", "status", "started_at", "finished_at", "last_error",
    },
    "opportunities.Opportunity": {
        # Set by the view from the logged-in user on first save.
        "created_by",
        # Provenance, written by opportunities.research when AI finds a
        # listing. Shown read-only on the form with the pages it read - an
        # editor checks those sources, they don't retype them.
        "found_by_ai", "sources",
    },
    "opportunities.Tag": {
        # Both written by readers.interests when someone types an interest
        # our list doesn't have. Shown on the Interests row, not editable:
        # "readers asked for this 12 times" is a fact, not a setting.
        "origin", "times_requested",
    },
    "readers.Reader": {
        # Shown as read-only text in reader_form.html (under "What AI read
        # into their free text") - inferred from the reader's own words,
        # not something an editor hand-edits.
        "ai_taste_summary", "ai_inferred_tags", "ai_avoid_tags", "ai_profile_updated_at",
    },
}


def editable_fields(model) -> set:
    return {
        field.name
        for field in model._meta.get_fields()
        if getattr(field, "editable", False) and not field.auto_created
    }


class DeskFormCoverageTests(TestCase):
    def test_every_editable_field_is_reachable_in_a_desk_form(self):
        forms = {
            Opportunity: OpportunityForm,
            Tag: TagForm,
            Reader: ReaderForm,
            Campaign: CampaignForm,
            EmailTemplate: EmailTemplateForm,
        }
        gaps = {}
        for model, form_class in forms.items():
            reachable = set(form_class.Meta.fields)
            label = model._meta.label
            allowed = set(INTENTIONALLY_ABSENT.get(label, {}))
            missing = editable_fields(model) - reachable - allowed
            if missing:
                gaps[label] = sorted(missing)

        self.assertEqual(
            gaps, {},
            "These model fields exist but no desk form reaches them. Add them to the "
            "form's Meta.fields, or to INTENTIONALLY_ABSENT with a reason:\n"
            + "\n".join(f"  {label}: {fields}" for label, fields in gaps.items()),
        )

    def test_siteconfig_form_excludes_only_updated_at(self):
        """SiteConfigForm uses Meta.exclude rather than an explicit field
        list (it has ~40 fields), so the coverage check above doesn't
        apply to it - this is the equivalent guard for that shape."""
        from siteconfig.models import SiteConfig

        from desk.forms import SiteConfigForm

        self.assertEqual(SiteConfigForm.Meta.exclude, ("updated_at",))
        # Every other editable field must actually resolve on the form.
        form = SiteConfigForm(instance=SiteConfig.load())
        missing = editable_fields(SiteConfig) - set(form.fields) - {"updated_at"}
        self.assertEqual(missing, set())

    def test_the_guard_actually_detects_a_missing_field(self):
        """The check above only earns its place if it can fail."""
        class StrippedOpportunityForm(OpportunityForm):
            class Meta(OpportunityForm.Meta):
                fields = tuple(f for f in OpportunityForm.Meta.fields if f != "critic_quote")

        missing = editable_fields(Opportunity) - set(StrippedOpportunityForm.Meta.fields)
        self.assertIn("critic_quote", missing)
