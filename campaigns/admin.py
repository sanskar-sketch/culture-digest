from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from opportunities.models import Category
from readers.models import Reader

from .models import Campaign, CampaignDelivery
from .sending import WEB_BUDGET_SECONDS, preview_for, send_campaign, send_test

STATUS_COLOURS = {
    Campaign.Status.DRAFT: "#8e8e93",
    Campaign.Status.SCHEDULED: "#0a84ff",
    Campaign.Status.SENDING: "#ff9500",
    Campaign.Status.SENT: "#34c759",
    Campaign.Status.CANCELLED: "#ff375f",
}


def pill(value, label):
    colour = STATUS_COLOURS.get(value, "#8e8e93")
    return format_html(
        '<span style="display:inline-block;padding:3px 10px;border-radius:999px;'
        'font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;'
        'color:{0};background:{0}22;">{1}</span>', colour, label)


class CampaignForm(forms.ModelForm):
    audience_categories = forms.MultipleChoiceField(
        choices=Category.choices, required=False, widget=forms.CheckboxSelectMultiple,
        label="Categories they follow",
        help_text="Only readers who follow at least one of these. Nothing ticked "
                  "means no restriction.")

    class Meta:
        model = Campaign
        fields = "__all__"
        widgets = {
            "brief": forms.Textarea(attrs={"rows": 7}),
            "body": forms.Textarea(attrs={"rows": 12}),
        }


class DeliveryInline(admin.TabularInline):
    model = CampaignDelivery
    extra = 0
    can_delete = False
    fields = ("reader", "status", "personalised", "sent_at", "provider_message_id", "error")
    readonly_fields = fields
    ordering = ("pk",)
    verbose_name_plural = "Who got what"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    form = CampaignForm
    change_form_template = "admin/campaigns/campaign_change_form.html"
    list_display = ("name", "status_pill", "audience_size", "send_at", "progress_summary",
                    "updated_at")
    list_display_links = ("name",)
    list_filter = ("status", "personalise")
    search_fields = ("name", "subject", "brief", "body")
    filter_horizontal = ("audience_tags",)
    readonly_fields = ("status", "started_at", "finished_at", "last_error", "created_by",
                       "created_at", "updated_at", "audience_summary", "results",
                       "what_happens")
    inlines = [DeliveryInline]
    save_on_top = True
    actions = ("schedule", "send_now", "cancel", "back_to_draft", "retry_failed")

    fieldsets = (
        ("The email", {
            "description": "Write a brief (the facts, why it matters, what to do) and let "
                           "AI write each reader's version - or write the body yourself. "
                           "Use Preview above to read it as a reader would.",
            "fields": ("name", "subject", "brief", "body", "personalise",
                       "link_label", "link_url"),
        }),
        ("Audience", {
            "description": "Leave everything empty to email every active reader.",
            "fields": ("audience_summary", "audience_categories", "audience_tags",
                       "audience_location"),
        }),
        ("Schedule", {
            "description": "Save, then press Schedule above. It goes at the time you set, "
                           "or straight away if you leave it blank. Send now skips the "
                           "queue.",
            "fields": ("what_happens", "send_at", "status"),
        }),
        ("Results", {
            "fields": ("results", "started_at", "finished_at", "last_error"),
        }),
        ("Metadata", {
            "classes": ("collapse",),
            "fields": ("created_by", "created_at", "updated_at"),
        }),
    )

    # --- columns --------------------------------------------------------

    @admin.display(description="Status", ordering="status")
    def status_pill(self, obj):
        return pill(obj.status, obj.get_status_display())

    @admin.display(description="Audience")
    def audience_size(self, obj):
        return obj.audience().count()

    @admin.display(description="Progress")
    def progress_summary(self, obj):
        p = obj.progress()
        if not p["total"]:
            return "—"
        bits = [f"{p['sent']} sent"]
        if p["failed"]:
            bits.append(f"{p['failed']} failed")
        if p["skipped"]:
            bits.append(f"{p['skipped']} skipped")
        if p["pending"] or p["sending"]:
            bits.append(f"{p['pending'] + p['sending']} to go")
        return " · ".join(bits)

    # --- readonly panels ------------------------------------------------

    @admin.display(description="Who gets it")
    def audience_summary(self, obj):
        if not obj or not obj.pk:
            return "Save the campaign to see who it would reach."
        count = obj.audience().count()
        return format_html(
            "<strong>{}</strong> reader{} right now. {}. The list is worked out when "
            "sending starts, so anyone who signs up before then is included.",
            count, "" if count == 1 else "s", obj.audience_description())

    @admin.display(description="What happens")
    def what_happens(self, obj):
        if not obj or not obj.pk:
            return "Save first, then Schedule or Send now from the buttons above."
        s = Campaign.Status
        if obj.status == s.DRAFT:
            return ("A draft. Nothing goes out until you press Schedule or Send now.")
        if obj.status == s.SCHEDULED:
            when = (f"at {timezone.localtime(obj.send_at):%a %-d %b %Y, %H:%M} ({timezone.get_current_timezone_name()})"
                    if obj.send_at else "on the scheduler's next pass, within 15 minutes")
            return f"Scheduled. It goes {when}. Cancel above if you change your mind."
        if obj.status == s.SENDING:
            return ("Being sent. The scheduler keeps going every 15 minutes until "
                    "everyone has theirs.")
        if obj.status == s.SENT:
            return "Sent. Retry failed deliveries with the action on the list page if needed."
        return "Cancelled. Schedule it again to reinstate it."

    @admin.display(description="Deliveries")
    def results(self, obj):
        if not obj or not obj.pk:
            return "—"
        p = obj.progress()
        if not p["total"]:
            return "Nothing sent yet."
        return format_html(
            "<strong>{}</strong> sent · {} failed · {} skipped · {} pending{}",
            p["sent"], p["failed"], p["skipped"], p["pending"],
            f" · {p['sending']} stuck mid-send" if p["sending"] else "")

    # --- saving ---------------------------------------------------------

    def get_inline_instances(self, request, obj=None):
        # An empty "Who got what" on the add page is noise.
        return super().get_inline_instances(request, obj) if obj else []

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    # --- buttons on the change page ------------------------------------

    def get_urls(self):
        wrap = self.admin_site.admin_view
        return [
            path("<int:pk>/preview/", wrap(self.preview), name="campaigns_campaign_preview"),
            path("<int:pk>/test-send/", wrap(self.test_send), name="campaigns_campaign_test_send"),
            path("<int:pk>/schedule/", wrap(self.schedule_one), name="campaigns_campaign_schedule"),
            path("<int:pk>/send-now/", wrap(self.send_one_now), name="campaigns_campaign_send_now"),
            path("<int:pk>/cancel/", wrap(self.cancel_one), name="campaigns_campaign_cancel"),
        ] + super().get_urls()

    def _back(self, pk):
        return redirect(reverse("admin:campaigns_campaign_change", args=[pk]))

    def _sample_reader(self, campaign, request):
        wanted = request.GET.get("reader") or request.POST.get("reader")
        if wanted:
            found = campaign.audience().filter(pk=wanted).first()
            if found:
                return found
        first = campaign.audience().first()
        if first:
            return first
        # No audience yet: a stand-in so the email can still be read.
        return Reader(name="Ada Example", email="ada@example.com", location="London",
                      interest_categories=["music"])

    def preview(self, request, pk):
        campaign = get_object_or_404(Campaign, pk=pk)
        reader = self._sample_reader(campaign, request)
        try:
            preview = preview_for(campaign, reader, budget_seconds=12)
            error = None
        except Exception as exc:
            preview, error = {}, str(exc)
        return render(request, "admin/campaigns/campaign_preview.html", {
            **self.admin_site.each_context(request),
            "title": f"Preview: {campaign.name}",
            "campaign": campaign, "reader": reader, "preview": preview, "error": error,
            "readers": list(campaign.audience()[:50]),
        })

    def test_send(self, request, pk):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        campaign = get_object_or_404(Campaign, pk=pk)
        if not request.user.email:
            self.message_user(request, "Your account has no email address - add one under "
                                       "Users first.", messages.ERROR)
            return self._back(pk)
        try:
            note = send_test(campaign, request.user.email, self._sample_reader(campaign, request))
            self.message_user(request, note, messages.SUCCESS)
        except Exception as exc:
            self.message_user(request, f"Test send failed: {exc}", messages.ERROR)
        return self._back(pk)

    def _schedule(self, request, campaign) -> bool:
        try:
            campaign.full_clean()
        except Exception as exc:
            self.message_user(request, f"{campaign}: can't schedule - {exc}", messages.ERROR)
            return False
        campaign.status = Campaign.Status.SCHEDULED
        campaign.save(update_fields=["status", "updated_at"])
        when = (f"for {timezone.localtime(campaign.send_at):%a %-d %b, %H:%M}"
                if campaign.send_at else "for the scheduler's next pass (within 15 minutes)")
        self.message_user(request, f"{campaign}: scheduled {when}.", messages.SUCCESS)
        return True

    def _send_now(self, request, campaign) -> None:
        if campaign.status not in (Campaign.Status.SCHEDULED, Campaign.Status.SENDING):
            try:
                campaign.full_clean()
            except Exception as exc:
                self.message_user(request, f"{campaign}: can't send - {exc}", messages.ERROR)
                return
            campaign.status = Campaign.Status.SCHEDULED
            campaign.send_at = None
            campaign.save(update_fields=["status", "send_at", "updated_at"])
        run = send_campaign(campaign, budget_seconds=WEB_BUDGET_SECONDS)
        level = messages.SUCCESS if run.sent or run.finished else messages.WARNING
        if run.failed:
            level = messages.ERROR
        self.message_user(request, f"{campaign}: {run.message}", level)

    def schedule_one(self, request, pk):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        self._schedule(request, get_object_or_404(Campaign, pk=pk))
        return self._back(pk)

    def send_one_now(self, request, pk):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        self._send_now(request, get_object_or_404(Campaign, pk=pk))
        return self._back(pk)

    def cancel_one(self, request, pk):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        campaign = get_object_or_404(Campaign, pk=pk)
        campaign.status = Campaign.Status.CANCELLED
        campaign.save(update_fields=["status", "updated_at"])
        self.message_user(request, f"{campaign}: cancelled. Anyone not yet emailed won't be.",
                          messages.SUCCESS)
        return self._back(pk)

    # --- list actions ---------------------------------------------------

    @admin.action(description="Schedule (goes at its send time, or straight away)")
    def schedule(self, request, queryset):
        for campaign in queryset:
            self._schedule(request, campaign)

    @admin.action(description="Send now (really emails the audience)")
    def send_now(self, request, queryset):
        for campaign in queryset:
            self._send_now(request, campaign)

    @admin.action(description="Cancel")
    def cancel(self, request, queryset):
        n = queryset.exclude(status=Campaign.Status.SENT).update(
            status=Campaign.Status.CANCELLED, updated_at=timezone.now())
        self.message_user(request, f"Cancelled {n}.", messages.SUCCESS)

    @admin.action(description="Back to draft")
    def back_to_draft(self, request, queryset):
        n = queryset.exclude(status=Campaign.Status.SENDING).update(
            status=Campaign.Status.DRAFT, updated_at=timezone.now())
        self.message_user(request, f"Moved {n} back to draft.", messages.SUCCESS)

    @admin.action(description="Retry failed or stuck deliveries")
    def retry_failed(self, request, queryset):
        for campaign in queryset:
            n = campaign.deliveries.filter(
                status__in=[CampaignDelivery.Status.FAILED, CampaignDelivery.Status.SENDING]
            ).update(status=CampaignDelivery.Status.PENDING, error="")
            if n:
                campaign.status = Campaign.Status.SCHEDULED
                campaign.save(update_fields=["status", "updated_at"])
            self.message_user(
                request,
                f"{campaign}: {n} delivery{'' if n == 1 else 'ies'} queued again."
                + (" The scheduler picks them up within 15 minutes." if n else ""),
                messages.SUCCESS if n else messages.INFO)
