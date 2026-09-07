from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from recommendations.emailing import send_welcome

from .forms import ReaderOnboardingForm
from .models import Reader

EMPTY = (None, "", [], {})


@require_http_methods(["GET", "POST"])
def onboarding_view(request):
    """The signup questionnaire.

    Signing up again with an address we already know *merges* rather than
    replaces: this form arrives blank, so a returning reader who fills in
    only their email would otherwise silently wipe everything they told us
    last time. Merging can add or change an answer but never clear one -
    removing an interest is an editorial action in the admin.
    """
    email = (request.POST.get("email") or "").strip().lower() if request.method == "POST" else ""
    existing = Reader.objects.filter(email=email).first() if email else None

    form = ReaderOnboardingForm(request.POST or None, instance=existing)

    if request.method == "POST" and form.is_valid():
        if existing is None:
            reader = form.save()
            send_welcome(reader)
        else:
            reader = _merge_into(form, existing)
        return render(
            request, "onboarding/thank_you.html",
            {"reader": reader, "returning": existing is not None},
        )

    return render(request, "onboarding/questionnaire.html", {"form": form})


def _merge_into(form, reader):
    """Apply only the answers this submission actually filled in.

    The form was bound to this instance so the unique-email check would
    pass, and validation has already written the blanks onto it in memory -
    so re-read the stored values before deciding what to keep.
    """
    reader = Reader.objects.get(pk=reader.pk)
    m2m = {"interest_tags"}
    for field, value in form.cleaned_data.items():
        if field in m2m or value in EMPTY or value is False:
            continue
        setattr(reader, field, value)
    reader.save()

    picked = form.cleaned_data.get("interest_tags")
    if picked:
        reader.interest_tags.add(*picked)
    return reader


def unsubscribe_view(request, token):
    reader = get_object_or_404(Reader, unsubscribe_token=token)
    if request.method == "POST":
        reader.is_active = False
        reader.save(update_fields=["is_active"])
    return render(request, "onboarding/unsubscribe.html", {"reader": reader})
