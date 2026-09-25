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
# Picked from a list rather than chosen one-of, so it needs its own widget.
MULTI_SETTINGS = {"availability": ("When I'm free", Reader.Availability.choices)}


def preference_field_name(key, setting: str) -> str:
    """A tag's field is pref_12_budget; a whole category's is pref_cat_music_budget."""
    return f"pref_{key}_{setting}"


# Signup shows these as chips, so they need to be short. The stored values
# are the same ones the desk's longer labels describe; scale and taste
# offer the two ends and the middle, which is what people actually pick.
CHIP_CHOICES = {
    "travel_radius": [("local_only", "Close by"), ("within_city", "Across my city"),
                      ("regional", "Around the region"), ("anywhere", "Anywhere")],
    "budget": [("free_cheap", "Free or cheap"), ("moderate", "Moderate"),
               ("treat", "Treat myself"), ("no_limit", "No limit")],
    "scale_preference": [(1, "Small rooms"), (3, "Either"), (5, "Big venues")],
    "mainstream_preference": [(1, "Mainstream"), (3, "Either"), (5, "Niche")],
}
USUAL_CHIP = ("", "Usual")


def rank_field_name(key) -> str:
    return f"rank_{key}"


def category_key(value: str) -> str:
    return f"cat_{value}"


class InterestPreferenceFields:
    """Per-interest travel, spend and taste settings on a reader form.

    One set of fields per interest, every one optional: blank means "same
    as my usual", so the reader only says what actually differs. Shared by
    the signup questionnaire and the desk, so an editor sees exactly what
    the reader chose.
    """

    # Signup: chips a thumb can hit. The desk overrides this with dropdowns.
    PREFERENCE_STYLE = "chips"

    def build_preference_fields(self, reader=None, tags=None, categories=None):
        """Fields for every category and every interest on offer.

        Each gets a rank - where the reader puts it among everything they
        picked - and its own travel, spend, scale, taste and timing, all
        "usual" until changed. Categories are here too: someone who only
        picked "music" and never went as far as "jazz" still gets to rank
        music and say what they'd spend on it.
        """
        self.preference_tags = list(tags if tags is not None else Tag.objects.all())
        if categories is None:
            categories = [c for c in Category.choices if c[0] != Category.OTHER]
        self.preference_categories = list(categories)

        self._stored_by_tag, self._stored_by_category = {}, {}
        if reader is not None and reader.pk:
            for stored in reader.interest_preferences.all():
                if stored.tag_id:
                    self._stored_by_tag[stored.tag_id] = stored
                else:
                    self._stored_by_category[stored.category] = stored

        for value, label in self.preference_categories:
            self._add_preference_fields(category_key(value), f"{label.lower()} in general",
                                        self._stored_by_category.get(value))
        for tag in self.preference_tags:
            self._add_preference_fields(tag.pk, tag.name, self._stored_by_tag.get(tag.pk))

    def _add_preference_fields(self, key, what: str, current):
        chips = self.PREFERENCE_STYLE == "chips"
        for setting, label, choices in PREFERENCE_SETTINGS:
            name = preference_field_name(key, setting)
            if chips:
                self.fields[name] = forms.ChoiceField(
                    choices=[USUAL_CHIP, *CHIP_CHOICES[setting]], required=False,
                    widget=forms.RadioSelect, label=f"{label} for {what}")
            else:
                self.fields[name] = forms.ChoiceField(
                    choices=[SAME_AS_USUAL, *choices], required=False,
                    label=f"{label} for {what}")
            stored = getattr(current, setting, None) if current is not None else None
            # "" ticks the Usual chip; a stored answer ticks its own.
            self.initial.setdefault(name, "" if stored in (None, "") else stored)
        for setting, (label, choices) in MULTI_SETTINGS.items():
            name = preference_field_name(key, setting)
            self.fields[name] = forms.MultipleChoiceField(
                choices=choices, required=False, widget=forms.CheckboxSelectMultiple,
                label=f"{label} for {what}",
                help_text="Leave all unticked for your usual answer.")
            if current is not None:
                self.initial.setdefault(name, getattr(current, setting) or [])
        rank_name = rank_field_name(key)
        self.fields[rank_name] = forms.IntegerField(
            required=False, min_value=1, max_value=999, label=f"Rank of {what}",
            widget=forms.HiddenInput if chips else forms.NumberInput(attrs={"min": 1}))
        if current is not None and current.rank:
            self.initial.setdefault(rank_name, current.rank)

    def preference_rows(self):
        """Every category and interest, as the templates need them, in the
        reader's own order where they have one."""
        rows = []
        for value, label in getattr(self, "preference_categories", []):
            stored = self._stored_by_category.get(value)
            rows.append(self._preference_row(category_key(value), label, "category", value,
                                             category=value, stored=stored))
        for tag in getattr(self, "preference_tags", []):
            rows.append(self._preference_row(tag.pk, tag.name, "interest", tag.pk,
                                             category=tag.category,
                                             stored=self._stored_by_tag.get(tag.pk)))
        # Ranked ones first, in rank order; the rest keep their places.
        return sorted(rows, key=lambda row: (row["rank"] is None, row["rank"] or 0))

    def _preference_row(self, key, name, kind, value, *, category, stored):
        return {
            "key": key, "name": name, "kind": kind, "value": value, "category": category,
            "rank": stored.rank if stored is not None else None,
            "rank_field": self[rank_field_name(key)],
            "fields": [{"field": self[preference_field_name(key, setting)], "label": label}
                       for setting, label, _ in PREFERENCE_SETTINGS],
            "choices": [{"field": self[preference_field_name(key, setting)], "label": label}
                        for setting, (label, _) in MULTI_SETTINGS.items()],
        }

    def _preference_values(self, key):
        values = {}
        for setting, _, _ in PREFERENCE_SETTINGS:
            raw = str(self.cleaned_data.get(preference_field_name(key, setting)) or "").strip()
            values[setting] = (int(raw) if raw else None) if setting in NUMERIC_SETTINGS else raw
        for setting in MULTI_SETTINGS:
            values[setting] = list(self.cleaned_data.get(preference_field_name(key, setting)) or [])
        return values

    def save_preferences(self, reader, *, merge: bool = False) -> int:
        """Store the ranks and exceptions. Returns how many interests carry one.

        `merge` is for a returning reader filling the signup form again:
        it only ever adds or changes an answer, never blanks one, the same
        way the rest of that form behaves.
        """
        followed_tags = set(reader.interest_tags.values_list("id", flat=True))
        followed_categories = set(reader.interest_categories or [])
        for value, _ in getattr(self, "preference_categories", []):
            self._store(reader, category_key(value), {"category": value},
                        kept=value in followed_categories, merge=merge)
        for tag in getattr(self, "preference_tags", []):
            self._store(reader, tag.pk, {"tag": tag},
                        kept=tag.pk in followed_tags, merge=merge)
        return reader.interest_preferences.count()

    def _store(self, reader, key, lookup, *, kept: bool, merge: bool):
        values = self._preference_values(key)
        rank = self.cleaned_data.get(rank_field_name(key)) or None
        said_something = any(v not in (None, "", []) for v in values.values())
        existing = InterestPreference.objects.filter(reader=reader, **lookup)
        if not kept:
            # Not something they follow: a rank or exception for it means nothing.
            existing.delete()
            return
        if not said_something and rank is None:
            if not merge:
                existing.delete()
            return
        row = existing.first() or InterestPreference(reader=reader, **lookup)
        for setting, value in values.items():
            if merge and value in (None, "", []):
                continue
            setattr(row, setting, value)
        if rank is not None or not merge:
            row.rank = rank
        row.save()


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
