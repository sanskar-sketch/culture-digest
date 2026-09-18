from django import forms

from opportunities.models import Category, Tag

from .models import InterestPreference, Reader

NO_PREFERENCE = ("", "No preference")
SAME_AS_USUAL = ("", "Same as usual")

SCALE_CHOICES = [(1, "Small rooms"), (2, "On the small side"), (3, "Either"),
                 (4, "On the big side"), (5, "Big venues")]
TASTE_CHOICES = [(1, "Mainstream"), (2, "Mostly mainstream"), (3, "Either"),
                 (4, "Mostly unusual"), (5, "Unusual and niche")]

# The settings a reader can vary interest by interest: everything the
# matching actually uses, in the order they're shown.
PREFERENCE_SETTINGS = (
    ("travel_radius", "How far", Reader.TravelRadius.choices),
    ("budget", "What I'd spend", Reader.Budget.choices),
    ("scale_preference", "Scale", SCALE_CHOICES),
    ("mainstream_preference", "Taste", TASTE_CHOICES),
)
NUMERIC_SETTINGS = {"scale_preference", "mainstream_preference"}


def preference_field_name(tag_id, setting: str) -> str:
    return f"pref_{tag_id}_{setting}"


class InterestPreferenceFields:
    """Per-interest travel, spend and taste settings on a reader form.

    One set of fields per interest, every one optional: blank means "same
    as my usual", so the reader only says what actually differs. Shared by
    the signup questionnaire and the desk, so an editor sees exactly what
    the reader chose.
    """

    def build_preference_fields(self, reader=None, tags=None):
        self.preference_tags = list(tags if tags is not None else Tag.objects.all())
        stored = {}
        if reader is not None and reader.pk:
            stored = {p.tag_id: p for p in reader.interest_preferences.all()}
        for tag in self.preference_tags:
            current = stored.get(tag.pk)
            for setting, label, choices in PREFERENCE_SETTINGS:
                name = preference_field_name(tag.pk, setting)
                self.fields[name] = forms.ChoiceField(
                    choices=[SAME_AS_USUAL, *choices], required=False,
                    label=f"{label} for {tag.name}")
                self.fields[name].widget.attrs["data-interest"] = tag.pk
                if current is not None:
                    self.initial.setdefault(name, getattr(current, setting) or "")

    def preference_rows(self):
        """[{tag, fields}] for the templates, in tag order."""
        rows = []
        for tag in getattr(self, "preference_tags", []):
            rows.append({
                "tag": tag,
                "fields": [{"field": self[preference_field_name(tag.pk, setting)], "label": label}
                           for setting, label, _ in PREFERENCE_SETTINGS],
            })
        return rows

    def _preference_values(self, tag):
        values = {}
        for setting, _, _ in PREFERENCE_SETTINGS:
            raw = (self.cleaned_data.get(preference_field_name(tag.pk, setting)) or "").strip()
            values[setting] = (int(raw) if raw else None) if setting in NUMERIC_SETTINGS else raw
        return values

    def save_preferences(self, reader, *, merge: bool = False) -> int:
        """Store the exceptions. Returns how many interests carry one.

        `merge` is for a returning reader filling the signup form again:
        it only ever adds or changes an answer, never blanks one, the same
        way the rest of that form behaves.
        """
        chosen = set(reader.interest_tags.values_list("id", flat=True))
        for tag in getattr(self, "preference_tags", []):
            values = self._preference_values(tag)
            said_something = any(v not in (None, "") for v in values.values())
            if tag.pk not in chosen:
                # Not one of their interests: an exception for it means nothing.
                InterestPreference.objects.filter(reader=reader, tag=tag).delete()
                continue
            if merge and not said_something:
                continue
            if not said_something:
                InterestPreference.objects.filter(reader=reader, tag=tag).delete()
                continue
            row, _ = InterestPreference.objects.get_or_create(reader=reader, tag=tag)
            for setting, value in values.items():
                if merge and value in (None, ""):
                    continue
                setattr(row, setting, value)
            row.save()
        return reader.interest_preferences.count()


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


class ReaderOnboardingForm(InterestPreferenceFields, forms.ModelForm):
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
            "other_categories",
            "other_interests",
            "other_travel",
            "other_budget",
            "other_availability",
            "loved_examples",
            "disliked_examples",
            "notes",
        ]
        widgets = {
            "age": forms.NumberInput(attrs={"min": 1, "max": 120, "inputmode": "numeric"}),
            "loved_examples": forms.Textarea(attrs={"rows": 3}),
            "disliked_examples": forms.Textarea(attrs={"rows": 3}),
            "travel_destinations": forms.Textarea(attrs={"rows": 2}),
            "other_categories": forms.TextInput(attrs={
                "placeholder": "e.g. sport, literature, workshops"}),
            "other_interests": forms.TextInput(attrs={
                "placeholder": "e.g. baroque choral, natural wine, brutalist buildings"}),
            "other_travel": forms.TextInput(attrs={
                "placeholder": "e.g. anywhere on the Elizabeth line"}),
            "other_budget": forms.TextInput(attrs={
                "placeholder": "e.g. usually under £30, more for something special"}),
            "other_availability": forms.TextInput(attrs={
                "placeholder": "e.g. weekday lunchtimes, school holidays only"}),
            "notes": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "Anything at all — a night you're planning, someone "
                               "you'd be going with, something you're curious about, "
                               "a place you'll be that week, things to avoid.",
            }),
        }
        labels = {
            "other_categories": "Something else",
            "other_interests": "Something else",
            "other_travel": "Something else",
            "other_budget": "Something else",
            "other_availability": "Something else",
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
        "other_categories", "other_interests", "other_travel",
        "other_budget", "other_availability",
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
        self.build_preference_fields(reader=self.instance, tags=tags.queryset)

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_mainstream_preference(self):
        value = self.cleaned_data.get("mainstream_preference")
        return int(value) if value else None

    def clean_scale_preference(self):
        value = self.cleaned_data.get("scale_preference")
        return int(value) if value else None
