from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from .emails import EmailTemplate
from .models import SiteConfig


class EmailTemplateForm(forms.ModelForm):
    """Refuses to save a template that won't compile.

    A broken tag saved here would only surface at send time, on a real
    reader's email - better to catch it in the form.
    """

    class Meta:
        model = EmailTemplate
        fields = "__all__"
        widgets = {
            "html_body": forms.Textarea(attrs={"rows": 26, "style": "font-family:ui-monospace,monospace;font-size:12px;"}),
            "text_body": forms.Textarea(attrs={"rows": 14, "style": "font-family:ui-monospace,monospace;font-size:12px;"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
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


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    form = EmailTemplateForm
    change_form_template = "admin/siteconfig/tabbed_change_form.html"
    list_display = ("name", "kind", "in_use", "updated_at")
    list_filter = ("kind",)
    search_fields = ("name", "subject", "html_body", "notes")
    readonly_fields = ("placeholders", "created_at", "updated_at")
    save_on_top = True

    fieldsets = (
        ("Template", {"fields": ("name", "kind", "notes")}),
        ("Subject", {"fields": ("subject",)}),
        ("What you can use", {"fields": ("placeholders",)}),
        ("HTML version", {"fields": ("html_body",)}),
        ("Plain-text version", {"fields": ("text_body",)}),
        ("Metadata", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="In use", boolean=True)
    def in_use(self, obj):
        config = SiteConfig.load()
        return obj.pk in {config.welcome_template_id, config.newsletter_template_id,
                          config.campaign_template_id}

    @admin.display(description="Placeholders you can use")
    def placeholders(self, obj):
        kind = getattr(obj, "kind", None) or EmailTemplate.Kind.NEWSLETTER
        items = EmailTemplate.PLACEHOLDERS.get(kind, [])
        return format_html(
            "{}<p style=\"margin-top:10px;color:#8e8e93\">Anything you leave out simply "
            "won't appear. Keep the unsubscribe link — it has to be there.</p>",
            format_html_join(mark_safe(" "), "<code>{}</code>", ((i,) for i in items)),
        )

    def get_urls(self):
        return [
            path("<int:pk>/preview/", self.admin_site.admin_view(self.preview),
                 name="siteconfig_emailtemplate_preview"),
            path("start-from-builtin/<str:kind>/", self.admin_site.admin_view(self.from_builtin),
                 name="siteconfig_emailtemplate_from_builtin"),
        ] + super().get_urls()

    def from_builtin(self, request, kind):
        """Create a copy of the shipped email, so editing starts from the real
        thing rather than an empty box."""
        if kind not in EmailTemplate.Kind.values:
            self.message_user(request, f"Unknown kind {kind!r}.", messages.ERROR)
            return redirect(reverse("admin:siteconfig_emailtemplate_changelist"))
        html, text = EmailTemplate.load_file_defaults(kind)
        template = EmailTemplate.objects.create(
            name=f"{EmailTemplate.Kind(kind).label} (copy of built-in)",
            kind=kind, html_body=html, text_body=text,
            notes="Started from the built-in version. Edit freely — the original is untouched.",
        )
        self.message_user(request, "Copied the built-in version. Edit it below, then "
                                   "select it in Site configuration to start using it.",
                          messages.SUCCESS)
        return redirect(reverse("admin:siteconfig_emailtemplate_change", args=[template.pk]))

    def preview(self, request, pk):
        """Render with representative data, so a template can be checked
        without emailing anyone."""
        template = EmailTemplate.objects.get(pk=pk)
        context = _preview_context(template.kind)
        try:
            html, text = template.render(context)
            subject = template.render_subject(context) or "(uses the configured subject)"
            error = None
        except Exception as exc:
            html = text = ""
            subject = ""
            error = str(exc)
        return render(request, "admin/siteconfig/email_preview.html", {
            **self.admin_site.each_context(request),
            "title": f"Preview: {template.name}",
            "template_obj": template, "preview_html": html,
            "preview_text": text, "preview_subject": subject, "error": error,
        })


def _preview_context(kind):
    """Stand-in data for previews - never touches a real reader."""
    from types import SimpleNamespace

    config = SiteConfig.load()
    reader = SimpleNamespace(name="Ada", email="ada@example.com")
    base = {"reader": reader, "site_config": config,
            "unsubscribe_url": "https://example.com/unsubscribe/preview/"}

    if kind == EmailTemplate.Kind.WELCOME:
        return {**base, "summary": [("Following", "music, food"), ("Based in", "London")]}

    if kind == EmailTemplate.Kind.CAMPAIGN:
        from recommendations.emailing import _paragraphs

        body = ("Frieze is on this weekend, and the Sculpture Park is free.\n\n"
                "You said you'd travel for the right thing. This is the right thing.")
        return {**base, "first_name": "Ada", "body_text": body,
                "body_html": _paragraphs(body),
                "campaign": SimpleNamespace(subject="This weekend at Frieze",
                                            link_url="https://example.com/frieze",
                                            link_label="Plan the day")}

    rec = SimpleNamespace(
        opportunity=SimpleNamespace(
            title="Trio residency in a basement jazz room",
            get_category_display=lambda: "Music",
            location_area="London", price_display="£16"),
        rationale="A small room and a short set — the kind of thing you said you like.",
        booking_url="https://example.com/book",
        more_like_this_url="https://example.com/f/more",
        not_for_me_url="https://example.com/f/no",
        save_url="https://example.com/f/save",
        booked_url="https://example.com/f/booked",
    )
    return {**base, "recommendations": [rec]}


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    """A singleton, so the changelist is skipped - clicking through from the
    admin index lands straight on the one editable record."""

    save_on_top = True
    readonly_fields = ("updated_at", "last_sent_on", "wording_drift")
    change_form_template = "admin/siteconfig/tabbed_change_form.html"

    fieldsets = (
        ("Branding", {
            "description": "Shown on the public pages and in the newsletter.",
            "fields": ("site_name", "tagline", "hero_eyebrow", "hero_headline",
                       "hero_subhead"),
        }),
        ("Sending", {
            "description": "How many picks go out, to whom, and how often the same "
                           "thing may reappear.",
            "fields": ("recommendations_per_send", "min_recommendations",
                       "cooldown_days", "email_from", "subject_template",
                       "send_welcome_email", "welcome_subject"),
        }),
        ("Schedule", {
            "description": "When the newsletter goes out. A scheduler runs every 15 "
                           "minutes; on a send day it sends on its first pass after the "
                           "send hour, once. One-off emails are scheduled from Campaigns.",
            "fields": ("send_frequency", "send_weekday", "send_day_of_month",
                       "send_hour", "last_sent_on"),
        }),
        ("Email templates", {
            "description": "Leave these empty to use the built-in emails. To write your "
                           "own, go to Email templates, start from the built-in version, "
                           "then select it here.",
            "fields": ("welcome_template", "newsletter_template", "campaign_template"),
        }),
        ("Matching — what raises a score", {
            "description": "Higher numbers mean a stronger pull. These decide which "
                           "opportunities a reader actually sees.",
            "fields": ("weight_tag_overlap", "weight_category",
                       "weight_inferred_tag", "weight_critic_rating"),
        }),
        ("Matching — what lowers or excludes", {
            "fields": ("penalty_avoid_tag", "penalty_price_step", "max_price_distance",
                       "penalty_mainstream_gap", "penalty_scale_gap",
                       "penalty_out_of_area"),
        }),
        ("Learning from feedback", {
            "description": "How much each feedback click shifts future picks. This is "
                           "the loop that makes recommendations improve over time.",
            "fields": ("feedback_more_like_this", "feedback_saved", "feedback_booked",
                       "feedback_not_for_me", "dislike_drop_threshold"),
        }),
        ("AI assistance", {
            "description": "Every feature falls back to a deterministic template when "
                           "switched off or unavailable. API keys stay in the "
                           "environment, not here.",
            "fields": ("ai_enabled", "ai_write_rationales", "ai_write_campaigns",
                       "ai_interpret_readers", "ai_classify_opportunities", "ai_model",
                       "ai_timeout_seconds", "ai_send_budget_seconds"),
        }),
        ("Shipped wording", {
            "description": "Text that ships with a default. Defaults only apply when "
                           "this configuration is first created, so an improved default "
                           "never reaches an existing site on its own - this shows where "
                           "yours differs, and lets you take the newer wording if you "
                           "want it.",
            "fields": ("wording_drift",),
        }),
        ("Metadata", {"classes": ("collapse",), "fields": ("updated_at",)}),
    )

    @admin.display(description="Where your wording differs from the shipped default")
    def wording_drift(self, obj):
        drift = obj.drift_from_defaults() if obj and obj.pk else []
        if not drift:
            return format_html(
                '<span style="color:#34c759">Everything matches the shipped '
                'wording.</span>')

        rows = format_html_join(
            mark_safe(""),
            '<tr><td style="padding:6px 12px 6px 0;vertical-align:top;white-space:nowrap">'
            '<code>{}</code></td>'
            '<td style="padding:6px 12px 6px 0;vertical-align:top">{}</td>'
            '<td style="padding:6px 0;vertical-align:top;color:#8e8e93">{}</td></tr>',
            ((d["field"], d["current"] or "(empty)", d["shipped"]) for d in drift),
        )
        return format_html(
            '<table style="font-size:13px"><tr>'
            '<th style="text-align:left;padding-right:12px">Field</th>'
            '<th style="text-align:left;padding-right:12px">Yours</th>'
            '<th style="text-align:left">Shipped</th></tr>{}</table>'
            '<p style="margin-top:12px"><a class="button" href="{}">'
            'Take the shipped wording for these {} field(s)</a></p>',
            rows,
            reverse("admin:siteconfig_reset_wording"),
            len(drift),
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        config = SiteConfig.load()
        return redirect(reverse("admin:siteconfig_siteconfig_change", args=[config.pk]))

    def get_urls(self):
        return [
            path("reset-wording/", self.admin_site.admin_view(self.reset_wording),
                 name="siteconfig_reset_wording"),
        ] + super().get_urls()

    def reset_wording(self, request):
        config = SiteConfig.load()
        changed = config.reset_to_defaults()
        if changed:
            self.message_user(
                request,
                f"Restored the shipped wording for: {', '.join(changed)}.",
                messages.SUCCESS)
        else:
            self.message_user(request, "Nothing to restore - it already matches.",
                              messages.INFO)
        return redirect(
            reverse("admin:siteconfig_siteconfig_change", args=[config.pk]))
