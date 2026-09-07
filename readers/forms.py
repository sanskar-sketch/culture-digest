from django import forms

from opportunities.models import Category, Tag

from .models import Reader

NO_PREFERENCE = ("", "No preference")


class TagCategoryCheckboxes(forms.CheckboxSelectMultiple):
    """Stamps each tag checkbox with its category, so the onboarding flow can
    show only the interests relevant to the categories a reader picked."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tag_categories = {}

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        category = self._tag_categories.get(str(value))
        if category:
            option["attrs"]["data-category"] = category
        return option


class ReaderOnboardingForm(forms.ModelForm):
    """Every field except email is optional by design - a reader should be
    able to sign up with as little or as much detail as they want, and
    refine their profile later."""

    interest_categories = forms.MultipleChoiceField(
        choices=[c for c in Category.choices if c[0] != Category.OTHER],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Which of these pull you in?",
        help_text="We'll only ask about the ones you pick.",
    )
    interest_tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        widget=TagCategoryCheckboxes,
        required=False,
        label="What are you interested in?",
        help_text="Pick as many as apply.",
    )
    availability = forms.MultipleChoiceField(
        choices=Reader.Availability.choices,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="When are you generally free?",
    )
    travel_radius = forms.ChoiceField(
        choices=[NO_PREFERENCE, *Reader.TravelRadius.choices],
        widget=forms.RadioSelect,
        required=False,
        label="How far will you travel for something great?",
    )
    budget = forms.ChoiceField(
        choices=[NO_PREFERENCE, *Reader.Budget.choices],
        widget=forms.RadioSelect,
        required=False,
        label="What's your typical budget?",
    )
    mainstream_preference = forms.ChoiceField(
        choices=[NO_PREFERENCE, *[(i, i) for i in range(1, 6)]],
        widget=forms.RadioSelect,
        required=False,
        label="Mainstream crowd-pleasers, or the unusual and niche?",
    )
    scale_preference = forms.ChoiceField(
        choices=[NO_PREFERENCE, *[(i, i) for i in range(1, 6)]],
        widget=forms.RadioSelect,
        required=False,
        label="Intimate and small-scale, or big and large-scale?",
    )
    open_to_surprise = forms.BooleanField(
        required=False,
        label="Surprise me sometimes",
        help_text="Occasionally include a wildcard pick outside my usual taste.",
    )

    class Meta:
        model = Reader
        fields = [
            "email",
            "name",
            "age",
            "location",
            "travel_destinations",
            "travel_radius",
            "budget",
            "availability",
            "interest_categories",
            "interest_tags",
            "mainstream_preference",
            "scale_preference",
            "open_to_surprise",
            "loved_examples",
            "disliked_examples",
            "notes",
        ]
        widgets = {
            "age": forms.NumberInput(attrs={"min": 1, "max": 120, "inputmode": "numeric"}),
            "loved_examples": forms.Textarea(attrs={"rows": 3}),
            "disliked_examples": forms.Textarea(attrs={"rows": 3}),
            "travel_destinations": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "Anything at all — a night you're planning, someone "
                               "you'd be going with, something you're curious about, "
                               "a place you'll be that week, things to avoid.",
            }),
        }
        labels = {
            "email": "Email address",
            "age": "Age",
            "location": "Where do you live?",
            "travel_destinations": "Where do you love to travel to?",
            "loved_examples": "Tell us about a few things you've loved recently",
            "disliked_examples": "Anything that's really not for you?",
            "notes": "Anything else we should know?",
        }

    TEXT_INPUT_FIELDS = (
        "email", "name", "age", "location", "travel_destinations",
        "loved_examples", "disliked_examples", "notes",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.TEXT_INPUT_FIELDS:
            widget = self.fields[field_name].widget
            widget.attrs["class"] = ((widget.attrs.get("class", "") + " text-input").strip())

        tags = self.fields["interest_tags"]
        tags.widget._tag_categories = {
            str(pk): category
            for pk, category in tags.queryset.values_list("pk", "category")
        }

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_mainstream_preference(self):
        value = self.cleaned_data.get("mainstream_preference")
        return int(value) if value else None

    def clean_scale_preference(self):
        value = self.cleaned_data.get("scale_preference")
        return int(value) if value else None
