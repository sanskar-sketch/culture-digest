"""Editor-authored email templates.

An editor can write the newsletter and welcome emails in the admin instead
of them living in files only a developer can change. Each kind of email can
have several templates; the one selected in the site configuration is the
one that goes out, and if none is selected the built-in file template is
used - so this is additive and the product still works with the table empty.

Bodies are rendered with Django's template engine, because the newsletter
has to loop over recommendations. That means template source is effectively
trusted input: it can reach anything in the render context and load any
installed template library. Editing is therefore staff-only and gated on
the usual admin permissions - treat the right to edit these as equivalent
to the right to change site content, not as untrusted input.
"""

from __future__ import annotations

from django.db import models
from django.template import Context, Template, TemplateSyntaxError
from django.template.loader import render_to_string


class EmailTemplate(models.Model):
    """One editable version of one kind of email."""

    class Kind(models.TextChoices):
        WELCOME = "welcome", "Welcome email"
        NEWSLETTER = "newsletter", "Newsletter"

    # Where the built-in versions live, for "start from the current one".
    FILE_TEMPLATES = {
        Kind.WELCOME: ("emails/welcome.html", "emails/welcome.txt"),
        Kind.NEWSLETTER: ("emails/newsletter.html", "emails/newsletter.txt"),
    }

    PLACEHOLDERS = {
        Kind.WELCOME: [
            "{{ reader.name }}", "{{ reader.email }}", "{{ site_config.site_name }}",
            "{{ site_config.tagline }}", "{{ unsubscribe_url }}",
            "{% for label, value in summary %}…{% endfor %}",
        ],
        Kind.NEWSLETTER: [
            "{{ reader.name }}", "{{ site_config.site_name }}", "{{ unsubscribe_url }}",
            "{% for rec in recommendations %}…{% endfor %}",
            "{{ rec.opportunity.title }}", "{{ rec.rationale }}", "{{ rec.booking_url }}",
            "{{ rec.more_like_this_url }}", "{{ rec.not_for_me_url }}",
            "{{ rec.save_url }}", "{{ rec.booked_url }}",
        ],
    }

    name = models.CharField(
        max_length=120,
        help_text="For you, not the reader - e.g. 'Newsletter, shorter intro'.")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    subject = models.CharField(
        max_length=200, blank=True,
        help_text="Blank uses the subject from the site configuration.")
    html_body = models.TextField(help_text="The HTML version. This is what most readers see.")
    text_body = models.TextField(
        blank=True,
        help_text="The plain-text version. Worth keeping - some clients show it, and "
                  "having one helps deliverability.")

    notes = models.TextField(blank=True, help_text="Anything you want to remember about this version.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "name"]
        verbose_name = "email template"

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    # --- rendering ------------------------------------------------------

    def render(self, context: dict) -> tuple[str, str]:
        """Return (html, text). Raises TemplateSyntaxError on a broken body."""
        ctx = Context(context)
        html = Template(self.html_body).render(ctx)
        text = Template(self.text_body).render(Context(context)) if self.text_body else ""
        return html, text

    def render_subject(self, context: dict) -> str | None:
        if not self.subject:
            return None
        return Template(self.subject).render(Context(context))

    def check_syntax(self) -> str | None:
        """The error message if this template won't compile, else None."""
        for field, source in (("HTML", self.html_body), ("plain text", self.text_body)):
            if not source:
                continue
            try:
                Template(source)
            except TemplateSyntaxError as exc:
                return f"{field} body: {exc}"
        return None

    @classmethod
    def load_file_defaults(cls, kind) -> tuple[str, str]:
        """The bundled version of this email, as a starting point for editing."""
        html_name, text_name = cls.FILE_TEMPLATES[kind]
        return _read(html_name), _read(text_name)


def _read(template_name: str) -> str:
    """The raw source of a bundled template - not rendered, so the tags stay
    in place for the editor to work with."""
    from django.template.loader import get_template

    return get_template(template_name).template.source


def render_email(kind, context: dict, fallback_templates: tuple[str, str]) -> tuple[str, str, str | None]:
    """Render one email, preferring the template the editor selected.

    Returns (html, text, subject_override). Falls back to the bundled files
    when nothing is selected, and also when a selected template is broken -
    a mistyped tag should not stop the newsletter going out.
    """
    import logging

    from .models import SiteConfig

    logger = logging.getLogger(__name__)
    config = SiteConfig.load()
    chosen = (
        config.welcome_template if kind == EmailTemplate.Kind.WELCOME
        else config.newsletter_template
    )

    if chosen is not None:
        try:
            html, text = chosen.render(context)
            return html, text, chosen.render_subject(context)
        except Exception:
            logger.exception(
                "Email template %r failed to render; using the built-in one", chosen.name
            )

    html_name, text_name = fallback_templates
    return render_to_string(html_name, context), render_to_string(text_name, context), None
