from django import forms

from opportunities.models import Tag

from .models import Reader


class ReaderOnboardingForm(forms.ModelForm):
    interest_tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="What are you interested in?",
        help_text="Pick as many as apply.",
    )
    availability = forms.MultipleChoiceField(
        choices=Reader.Availability.choices,
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="When are you generally free?",
    )

    class Meta:
        model = Reader
        fields = [
            "email",
            "name",
            "location",
            "travel_radius",
            "budget",
            "availability",
            "interest_tags",
            "mainstream_preference",
            "scale_preference",
            "loved_examples",
            "disliked_examples",
        ]
        widgets = {
            "travel_radius": forms.RadioSelect,
            "budget": forms.RadioSelect,
            "mainstream_preference": forms.RadioSelect,
            "scale_preference": forms.RadioSelect,
            "loved_examples": forms.Textarea(attrs={"rows": 3}),
            "disliked_examples": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "email": "Email address",
            "location": "Where are you based? (city or area)",
            "travel_radius": "How far will you travel for something great?",
            "budget": "What's your typical budget?",
            "mainstream_preference": "Mainstream crowd-pleasers, or the unusual and niche?",
            "scale_preference": "Intimate and small-scale, or big and large-scale?",
            "loved_examples": "Tell us about a few things you've loved recently",
            "disliked_examples": "Anything that's really not for you?",
        }

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()
