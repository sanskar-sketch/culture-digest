from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from .forms import ReaderOnboardingForm
from .models import Reader


@require_http_methods(["GET", "POST"])
def onboarding_view(request):
    """The signup / onboarding questionnaire.

    Resubmitting with an email that's already registered updates that
    reader's profile in place, rather than erroring on the unique
    constraint — handy for readers who want to revise their taste profile.
    """
    instance = None
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        instance = Reader.objects.filter(email=email).first()

    form = ReaderOnboardingForm(request.POST or None, instance=instance)

    if request.method == "POST" and form.is_valid():
        reader = form.save()
        return render(request, "onboarding/thank_you.html", {"reader": reader})

    return render(request, "onboarding/questionnaire.html", {"form": form})


def unsubscribe_view(request, token):
    reader = get_object_or_404(Reader, unsubscribe_token=token)
    if request.method == "POST":
        reader.is_active = False
        reader.save(update_fields=["is_active"])
    return render(request, "onboarding/unsubscribe.html", {"reader": reader})
