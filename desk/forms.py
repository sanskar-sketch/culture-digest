"""Plain ModelForms for the desk. No admin machinery involved."""

from django import forms

from campaigns.models import Campaign
from opportunities.models import Category, Opportunity, Tag
from readers.models import Reader
from siteconfig.emails import EmailTemplate
from siteconfig.models import SiteConfig


def _style(fields):
    """Apply the desk's placeholder-free, class-free styling contract:
    inputs are styled globally by element/type, so this only needs to fix
    the handful of widgets that need a specific size or behaviour."""
    return fields


class OpportunityForm(forms.ModelForm):
    class Meta:
        model = Opportunity
        fields = (
            "title", "slug", "category", "status", "tags",
            "description", "editorial_note",
            "price_tier", "price_display", "location_name", "location_area",
            "is_online", "booking_url", "start_date", "end_date",
            "critic_rating", "critic_rating_source", "critic_quote",
            "mainstream_to_unusual", "intimate_to_large_scale",
        )
        widgets = {
            "slug": forms.TextInput(attrs={"placeholder": "Leave blank to generate from the title"}),
            "description": forms.Textarea(attrs={"rows": 5}),
            "editorial_note": forms.Textarea(attrs={"rows": 3}),
            "critic_quote": forms.Textarea(attrs={"rows": 2}),
            "tags": forms.CheckboxSelectMultiple,
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"].queryset = Tag.objects.all()
        if not self.instance.pk:
            self.fields["status"].initial = Opportunity.Status.DRAFT


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ("name", "slug", "category")
        widgets = {"slug": forms.TextInput(attrs={"placeholder": "Leave blank to generate from the name"})}


# Fields that make up a reader's taste profile - same list the old admin
# used for the profile-completeness meter.
READER_PROFILE_FIELDS = (
    "name", "age", "location", "travel_destinations", "travel_radius",
    "budget", "availability", "mainstream_preference", "scale_preference",
    "loved_examples", "disliked_examples", "notes",
)


class ReaderForm(forms.ModelForm):
    class Meta:
        model = Reader
        fields = (
            "email", "name", "age", "is_active",
            "location", "travel_radius", "other_travel", "travel_destinations",
            "interest_categories", "interest_tags", "mainstream_preference",
            "scale_preference", "open_to_surprise", "other_categories",
            "other_interests", "loved_examples", "disliked_examples", "notes",
            "budget", "other_budget", "availability", "other_availability",
        )
        widgets = {
            "interest_tags": forms.CheckboxSelectMultiple,
            "loved_examples": forms.Textarea(attrs={"rows": 3}),
            "disliked_examples": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "other_travel": forms.Textarea(attrs={"rows": 2}),
            "travel_destinations": forms.Textarea(attrs={"rows": 2}),
            "other_categories": forms.Textarea(attrs={"rows": 2}),
            "other_interests": forms.Textarea(attrs={"rows": 2}),
            "other_budget": forms.Textarea(attrs={"rows": 2}),
            "other_availability": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["interest_tags"].queryset = Tag.objects.all()


class CampaignForm(forms.ModelForm):
    audience_categories = forms.MultipleChoiceField(
        choices=Category.choices, required=False, widget=forms.CheckboxSelectMultiple,
        label="Categories they follow",
        help_text="Only readers who follow at least one of these. Nothing ticked means "
                  "no restriction.")

    class Meta:
        model = Campaign
        fields = (
            "name", "subject", "brief", "body", "personalise", "link_label", "link_url",
            "audience_categories", "audience_tags", "audience_location", "send_at",
        )
        widgets = {
            "brief": forms.Textarea(attrs={"rows": 6}),
            "body": forms.Textarea(attrs={"rows": 10}),
            "audience_tags": forms.CheckboxSelectMultiple,
            "send_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["audience_tags"].queryset = Tag.objects.all()
        self.fields["send_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        if self.instance.pk:
            self.fields["audience_categories"].initial = self.instance.audience_categories


class EmailTemplateForm(forms.ModelForm):
    class Meta:
        model = EmailTemplate
        fields = ("name", "kind", "subject", "html_body", "text_body", "notes")
        widgets = {
            "html_body": forms.Textarea(attrs={"rows": 22, "class": "d-mono"}),
            "text_body": forms.Textarea(attrs={"rows": 12, "class": "d-mono"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean(self):
        cleaned = super().clean()
        probe = EmailTemplate(
            kind=cleaned.get("kind") or EmailTemplate.Kind.NEWSLETTER,
            html_body=cleaned.get("html_body") or "",
            text_body=cleaned.get("text_body") or "",
        )
        error = probe.check_syntax()
        if error:
            raise forms.ValidationError(f"This template won't render — {error}")
        return cleaned


class SiteConfigForm(forms.ModelForm):
    class Meta:
        model = SiteConfig
        exclude = ("updated_at",)
        widgets = {
            "hero_subhead": forms.Textarea(attrs={"rows": 3}),
        }
