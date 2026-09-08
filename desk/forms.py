"""Plain ModelForms for the desk. No admin machinery involved."""

from django import forms

from campaigns.models import Campaign
from opportunities.models import Category, Opportunity, Tag
from readers.models import Reader
from siteconfig.emails import EmailTemplate
from siteconfig.models import SiteConfig


class FriendlyChoices:
    """Say what the blank option in a dropdown actually means.

    Django labels it "---------" everywhere, which tells the editor
    nothing. The option itself has to stay: on a required field it is the
    placeholder, and removing it would silently pre-select the first real
    choice - someone who never touches the taste dial would submit "1"
    without having chosen it. On an optional field it *is* the "none"
    value, and removing it would make the field impossible to clear.

    So the option stays and only its wording changes. Subclasses give
    per-field text in EMPTY_LABELS; anything else falls back to a prompt
    for required fields and "Not set" for optional ones.
    """

    EMPTY_LABELS: dict[str, str] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, (forms.CheckboxSelectMultiple, forms.SelectMultiple)):
                continue
            if not isinstance(field.widget, forms.Select):
                continue

            label = self.EMPTY_LABELS.get(
                name, "Choose one…" if field.required else "Not set")

            # A model choice field owns its blank option through empty_label.
            if isinstance(field, forms.ModelChoiceField):
                field.empty_label = label
                continue

            choices = list(field.choices)
            if choices and str(choices[0][0]) == "":
                field.choices = [("", label)] + choices[1:]


class OpportunityForm(FriendlyChoices, forms.ModelForm):
    EMPTY_LABELS = {
        "category": "Choose a category",
        "price_tier": "Choose a price tier",
        "mainstream_to_unusual": "Choose 1 (mainstream) to 5 (unusual)",
        "intimate_to_large_scale": "Choose 1 (intimate) to 5 (large-scale)",
    }

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


class TagForm(FriendlyChoices, forms.ModelForm):
    # Blank is meaningful here: the interest is offered to everyone.
    EMPTY_LABELS = {"category": "No category — offer it to everyone"}

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


class ReaderForm(FriendlyChoices, forms.ModelForm):
    EMPTY_LABELS = {
        "travel_radius": "Not set",
        "budget": "Not set",
        "mainstream_preference": "Not set",
        "scale_preference": "Not set",
    }

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


class CampaignForm(FriendlyChoices, forms.ModelForm):
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


class EmailTemplateForm(FriendlyChoices, forms.ModelForm):
    EMPTY_LABELS = {"kind": "Choose which email this is"}

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


class SiteConfigForm(FriendlyChoices, forms.ModelForm):
    # Empty means "no override", which is the built-in email.
    EMPTY_LABELS = {
        "welcome_template": "Use the built-in welcome email",
        "newsletter_template": "Use the built-in newsletter",
        "campaign_template": "Use the built-in campaign email",
    }

    class Meta:
        model = SiteConfig
        exclude = ("updated_at",)
        widgets = {
            "hero_subhead": forms.Textarea(attrs={"rows": 3}),
        }
