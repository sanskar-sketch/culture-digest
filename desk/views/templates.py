from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from desk.forms import EmailTemplateForm
from desk.permissions import staff_required
from desk.utils import paginate, search
from siteconfig.emails import EmailTemplate
from siteconfig.models import SiteConfig


@staff_required
def template_list(request):
    qs = EmailTemplate.objects.all()
    qs = search(qs, request, ["name", "subject", "html_body", "notes"])
    page_obj = paginate(request, qs.order_by("kind", "name"))
    config = SiteConfig.load()
    in_use_ids = {config.welcome_template_id, config.newsletter_template_id, config.campaign_template_id}
    for t in page_obj:
        t.in_use = t.pk in in_use_ids

    context = {
        "page_title": "Email designs",
        "page_blurb": "How the welcome, newsletter and campaign emails look. Nothing here "
                      "means the built-in designs are used, which is fine.",
        "breadcrumbs": [("Settings", reverse("desk:siteconfig")), ("Email designs", None)],
        "page_obj": page_obj,
        "result_count": qs.count(),
        "search_placeholder": "Search templates…",
        "kinds": EmailTemplate.Kind.choices,
        "add_url": reverse("desk:templates_add"),
    }
    return render(request, "desk/template_list.html", context)


@staff_required
def template_from_builtin(request, kind):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if kind not in EmailTemplate.Kind.values:
        messages.error(request, f"Unknown kind {kind!r}.")
        return redirect("desk:templates_list")
    html, text = EmailTemplate.load_file_defaults(kind)
    template = EmailTemplate.objects.create(
        name=f"{EmailTemplate.Kind(kind).label} (copy of built-in)", kind=kind,
        html_body=html, text_body=text,
        notes="Started from the built-in version. Edit freely - the original is untouched.")
    messages.success(request, "Copied the built-in version. Edit it below, then select it "
                              "in Site configuration to start using it.")
    return redirect("desk:templates_change", pk=template.pk)


@staff_required
def template_form(request, pk=None):
    instance = get_object_or_404(EmailTemplate, pk=pk) if pk else None
    if request.method == "POST":
        form = EmailTemplateForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f"Saved “{obj.name}”.")
            return redirect("desk:templates_change", pk=obj.pk)
    else:
        form = EmailTemplateForm(instance=instance)

    placeholders = EmailTemplate.PLACEHOLDERS.get(
        instance.kind if instance else EmailTemplate.Kind.NEWSLETTER, [])
    context = {
        "page_title": "Add email design" if not instance else instance.name,
        "breadcrumbs": [("Settings", reverse("desk:siteconfig")),
                        ("Email designs", reverse("desk:templates_list")),
                        ("Add" if not instance else instance.name, None)],
        "form": form,
        "instance": instance,
        "placeholders": placeholders,
    }
    return render(request, "desk/template_form.html", context)


@staff_required
def template_preview(request, pk):
    from types import SimpleNamespace

    template = get_object_or_404(EmailTemplate, pk=pk)
    config = SiteConfig.load()
    reader = SimpleNamespace(name="Ada", email="ada@example.com")
    base = {"reader": reader, "site_config": config,
            "unsubscribe_url": "https://example.com/unsubscribe/preview/"}

    if template.kind == EmailTemplate.Kind.WELCOME:
        context = {**base, "summary": [("Following", "music, food"), ("Based in", "London")]}
    elif template.kind == EmailTemplate.Kind.CAMPAIGN:
        from recommendations.emailing import _paragraphs

        body = ("Frieze is on this weekend, and the Sculpture Park is free.\n\n"
                "You said you'd travel for the right thing. This is the right thing.")
        context = {**base, "first_name": "Ada", "body_text": body, "body_html": _paragraphs(body),
                   "campaign": SimpleNamespace(subject="This weekend at Frieze",
                                               link_url="https://example.com/frieze",
                                               link_label="Plan the day")}
    else:
        rec = SimpleNamespace(
            opportunity=SimpleNamespace(title="Trio residency in a basement jazz room",
                                        get_category_display=lambda: "Music",
                                        location_area="London", price_display="£16"),
            rationale="A small room and a short set - the kind of thing you said you like.",
            booking_url="https://example.com/book", more_like_this_url="https://example.com/f/more",
            not_for_me_url="https://example.com/f/no", save_url="https://example.com/f/save",
            booked_url="https://example.com/f/booked")
        context = {**base, "recommendations": [rec]}

    try:
        html, text = template.render(context)
        subject = template.render_subject(context) or "(uses the configured subject)"
        error = None
    except Exception as exc:
        html = text = ""
        subject = ""
        error = str(exc)

    return render(request, "desk/template_preview.html", {
        "page_title": f"Preview: {template.name}",
        "breadcrumbs": [("Settings", reverse("desk:siteconfig")),
                        ("Email designs", reverse("desk:templates_list")),
                        (template.name, reverse("desk:templates_change", args=[pk])), ("Preview", None)],
        "template_obj": template, "preview_html": html, "preview_text": text,
        "preview_subject": subject, "error": error,
    })
