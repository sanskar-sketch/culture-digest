"""Templates: a send set up once, kept to use again.

Two things start from here. A campaign can begin as a template
("Start a campaign from this"), and the scheduler runs any template with
a frequency. Everything the template does on its own - picking an
audience, suggesting interests, writing the body - is a switch the editor
can turn off, and every result of those switches is shown on the form
before anything is sent.
"""

from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from campaigns import templates_runner
from campaigns.models import Campaign, SavedTemplate
from campaigns.sending import preview_for
from desk.forms import SavedTemplateForm
from desk.permissions import staff_required
from desk.utils import filter_options, paginate, search
from recommendations.sending import preview_issue_for_reader
from siteconfig.emails import EmailTemplate


def subnav(active):
    return [
        ("Templates", reverse("desk:saved_templates_list"), active == "templates",
         SavedTemplate.objects.count()),
        ("Email designs", reverse("desk:templates_list"), active == "designs",
         EmailTemplate.objects.count()),
    ]


@staff_required
def template_list(request):
    qs = SavedTemplate.objects.all().prefetch_related("audience_tags", "readers")
    qs = search(qs, request, ["name", "subject", "brief", "body"])
    kind = request.GET.get("kind")
    if kind in SavedTemplate.Kind.values:
        qs = qs.filter(kind=kind)
    status = request.GET.get("status")
    if status in SavedTemplate.Status.values:
        qs = qs.filter(status=status)

    page_obj = paginate(request, qs)
    for t in page_obj:
        t.audience_size = t.audience().count()
        t.due, t.due_why = t.due_for_a_run()

    context = {
        "page_title": "Templates",
        "page_blurb": "Sends you've set up once. Run one now, let it run on a schedule, "
                      "or start a campaign from it.",
        "breadcrumbs": [("Templates", None)],
        "subnav": subnav("templates"),
        "page_obj": page_obj,
        "result_count": qs.count(),
        "search_placeholder": "Search templates…",
        "filter_groups": [
            {"title": "Kind", "param": "kind",
             "options": filter_options(request, "kind", SavedTemplate.Kind.choices)},
            {"title": "Status", "param": "status",
             "options": filter_options(request, "status", SavedTemplate.Status.choices)},
        ],
        "has_active_filters": bool(request.GET.get("kind") or request.GET.get("status")),
        "add_url": reverse("desk:saved_templates_add"),
    }
    return render(request, "desk/saved_template_list.html", context)


def _apply_suggested_tags(request, template):
    """auto_tag: on save, let AI propose the interests the brief suits."""
    from opportunities.models import Category, Tag
    from recommendations import ai

    if not (template.auto_tag and template.kind == SavedTemplate.Kind.CAMPAIGN
            and (template.brief.strip() or template.body.strip())):
        return
    if not ai.is_enabled("write_campaigns"):
        return
    suggestion = ai.suggest_audience(template)
    if not suggestion:
        messages.warning(request, "AI couldn't suggest interests this time - see the AI "
                                  "row under Settings → Health.")
        return
    tags = list(Tag.objects.filter(slug__in=suggestion.get("tags") or []))
    if tags:
        template.audience_tags.set(tags)
    allowed = {v for v, _ in Category.choices}
    categories = [c for c in suggestion.get("categories") or [] if c in allowed]
    if categories and not template.audience_categories:
        template.audience_categories = categories
        template.save(update_fields=["audience_categories", "updated_at"])
    messages.info(request, "AI suggested interests: "
                  + (", ".join(t.name for t in tags) or "none that fit")
                  + ". Change them on the Who tab if that's wrong.")


@staff_required
def template_form(request, pk=None):
    instance = get_object_or_404(SavedTemplate, pk=pk) if pk else None
    if request.method == "POST":
        form = SavedTemplateForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            if not obj.pk:
                obj.created_by = request.user
            obj.audience_categories = form.cleaned_data["audience_categories"]
            obj.save()
            form.save_m2m()
            messages.success(request, f"Saved “{obj.name}”.")
            _apply_suggested_tags(request, obj)
            return redirect("desk:saved_templates_change", pk=obj.pk)
    else:
        initial = {}
        if not instance:
            initial = {"kind": request.GET.get("kind") or SavedTemplate.Kind.NEWSLETTER}
        form = SavedTemplateForm(instance=instance, initial=initial)

    context = {
        "page_title": "New template" if not instance else instance.name,
        "breadcrumbs": [("Templates", reverse("desk:saved_templates_list")),
                        ("New" if not instance else instance.name, None)],
        "form": form,
        "instance": instance,
        "audience_count": instance.audience().count() if instance else None,
        "audience_description": instance.audience_description() if instance else None,
        "due": instance.due_for_a_run() if instance else None,
    }
    return render(request, "desk/saved_template_form.html", context)


@staff_required
def template_preview(request, pk):
    """What the first reader in the audience would get. Sends nothing."""
    template = get_object_or_404(SavedTemplate, pk=pk)
    readers = list(template.audience()[:50])
    wanted = request.GET.get("reader")
    reader = next((r for r in readers if str(r.pk) == wanted), readers[0] if readers else None)

    preview, error = None, None
    if reader is None:
        error = "Nobody matches this template's audience yet."
    elif template.kind == SavedTemplate.Kind.NEWSLETTER:
        built = preview_issue_for_reader(reader)
        if built["ok"]:
            preview = {"subject": built["subject"], "html": built["html"],
                       "text": built["text"], "picks": built["picks"]}
        else:
            error = built["message"]
    else:
        # A campaign that exists only for this render: nothing is saved.
        stub = Campaign(name=template.name, subject=template.subject, brief=template.brief,
                        body=template.body, personalise=template.auto_write,
                        link_label=template.link_label, link_url=template.link_url)
        try:
            preview = preview_for(stub, reader, budget_seconds=12)
        except Exception as exc:
            error = str(exc)

    return render(request, "desk/saved_template_preview.html", {
        "page_title": f"Preview: {template.name}",
        "breadcrumbs": [("Templates", reverse("desk:saved_templates_list")),
                        (template.name, reverse("desk:saved_templates_change", args=[pk])),
                        ("Preview", None)],
        "template_obj": template, "reader": reader, "readers": readers,
        "preview": preview, "error": error,
    })


@staff_required
def template_run(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    template = get_object_or_404(SavedTemplate, pk=pk)
    report = templates_runner.run(template, created_by=request.user)
    (messages.success if report["ok"] and not report["failed"] else messages.warning)(
        request, report["message"])
    if report.get("campaign"):
        return redirect("desk:campaigns_change", pk=report["campaign"].pk)
    return redirect("desk:saved_templates_change", pk=pk)


@staff_required
def template_suggest_audience(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    template = get_object_or_404(SavedTemplate, pk=pk)
    from recommendations import ai

    if not ai.is_enabled("write_campaigns"):
        messages.warning(request, "AI is not configured, or campaign writing is switched "
                                  "off in Settings → AI assistance.")
        return redirect("desk:saved_templates_change", pk=pk)
    was = template.auto_select_audience
    template.auto_select_audience = True
    note = templates_runner.refresh_audience(template)
    template.auto_select_audience = was
    template.save(update_fields=["auto_select_audience", "updated_at"])
    (messages.success if note and note.startswith("AI set") else messages.warning)(
        request, note or "AI could not pick an audience.")
    return redirect("desk:saved_templates_change", pk=pk)


@staff_required
def template_to_campaign(request, pk):
    """Start a real campaign from this template, ready to review and send."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    template = get_object_or_404(SavedTemplate, pk=pk)
    if template.kind != SavedTemplate.Kind.CAMPAIGN:
        messages.warning(request, "Only a campaign template can become a campaign. A "
                                  "newsletter template is run from here instead.")
        return redirect("desk:saved_templates_change", pk=pk)
    campaign = template.make_campaign(created_by=request.user)
    messages.success(request, f"Started “{campaign.name}” from the template. Read it, "
                              "change anything, then Preview and Send.")
    return redirect("desk:campaigns_change", pk=campaign.pk)
