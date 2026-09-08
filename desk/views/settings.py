from django.contrib import messages
from django.shortcuts import redirect, render

from desk.forms import SiteConfigForm
from desk.permissions import staff_required
from siteconfig.models import SiteConfig


@staff_required
def siteconfig_form(request):
    config = SiteConfig.load()
    if request.method == "POST":
        form = SiteConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Saved.")
            return redirect("desk:siteconfig")
    else:
        form = SiteConfigForm(instance=config)

    context = {
        "page_title": "Site configuration",
        "breadcrumbs": [("Site configuration", None)],
        "form": form,
        "config": config,
        "drift": config.drift_from_defaults(),
    }
    return render(request, "desk/siteconfig_form.html", context)


@staff_required
def reset_wording(request):
    config = SiteConfig.load()
    changed = config.reset_to_defaults()
    if changed:
        messages.success(request, f"Restored the shipped wording for: {', '.join(changed)}.")
    else:
        messages.info(request, "Nothing to restore - it already matches.")
    return redirect("desk:siteconfig")
