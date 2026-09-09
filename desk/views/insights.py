"""What readers actually want, and what they actually do.

Two different questions, and the page keeps them apart:

* what they *said* - the interests they picked, the categories they
  follow, the feedback buttons they pressed;
* what they *did* - which picks they opened, and which part of the email
  they used to do it.

The gap between the two is the useful bit. An interest fifty readers
picked and nobody ever clicks is not the same as one nobody picked and
everybody opens.

Every figure is a bounded aggregate, computed on load. There is no cache
here for the same reason there is none on the Overview: a number you
cannot trust to be current is worse than one that costs a query.
"""

from django.db.models import Count, F, Q
from django.shortcuts import render

from campaigns.models import Campaign
from desk.permissions import staff_required
from opportunities.models import Category, Tag
from readers.models import Reader
from recommendations.models import LinkClick, NewsletterIssue, Recommendation

TOP = 12


def _percent(part, whole):
    return round(part / whole * 100) if whole else 0


@staff_required
def insights(request):
    readers_total = Reader.objects.filter(is_active=True).count()

    # --- what they said ------------------------------------------------
    top_interests = list(
        Tag.objects
        .annotate(picked=Count("interested_readers", distinct=True),
                  inferred=Count("ai_inferred_readers", distinct=True),
                  live=Count("opportunities",
                             filter=Q(opportunities__status="published"), distinct=True))
        .filter(Q(picked__gt=0) | Q(inferred__gt=0))
        .order_by("-picked", "-inferred")[:TOP]
    )
    for tag in top_interests:
        tag.share = _percent(tag.picked, readers_total)

    # Asked for by the public, and we have nothing to send. The work queue.
    unmet = list(
        Tag.objects.filter(origin=Tag.Origin.READER, opportunities__isnull=True)
        .order_by("-times_requested", "name")[:TOP]
    )

    followed = {}
    for row in Reader.objects.filter(is_active=True).values_list("interest_categories", flat=True):
        for value in row or []:
            followed[value] = followed.get(value, 0) + 1
    labels = dict(Category.choices)
    category_demand = sorted(
        ({"label": labels.get(v, v), "readers": n, "share": _percent(n, readers_total)}
         for v, n in followed.items()),
        key=lambda r: -r["readers"])

    # --- what they did -------------------------------------------------
    recs_total = Recommendation.objects.count()
    feedback = Recommendation.objects.aggregate(
        more=Count("id", filter=Q(feedback=Recommendation.Feedback.MORE_LIKE_THIS)),
        booked=Count("id", filter=Q(feedback=Recommendation.Feedback.BOOKED)),
        saved=Count("id", filter=Q(feedback=Recommendation.Feedback.SAVE)),
        nope=Count("id", filter=Q(feedback=Recommendation.Feedback.NOT_FOR_ME)),
    )
    responded = sum(feedback.values())

    sections = list(
        LinkClick.objects.values("section")
        .annotate(clicks=Count("id"), people=Count("reader", distinct=True))
        .order_by("-clicks")
    )
    section_labels = dict(LinkClick.Section.choices)
    for row in sections:
        row["label"] = section_labels.get(row["section"], row["section"])

    clicks_total = LinkClick.objects.count()
    booking_clicks = LinkClick.objects.filter(section=LinkClick.Section.BOOKING).count()

    # Which picks people actually opened.
    most_clicked = list(
        LinkClick.objects
        .filter(section=LinkClick.Section.BOOKING, recommendation__isnull=False)
        .values(title=F("recommendation__opportunity__title"),
                listing_id=F("recommendation__opportunity_id"))
        .annotate(clicks=Count("id"), people=Count("reader", distinct=True))
        .order_by("-clicks")[:TOP]
    )

    # Recommended a lot and opened by nobody: the honest failure list.
    ignored = list(
        Recommendation.objects
        .values(title=F("opportunity__title"), listing_id=F("opportunity_id"))
        .annotate(sent=Count("id"),
                  clicks=Count("link_clicks", distinct=True),
                  replies=Count("id", filter=~Q(feedback=Recommendation.Feedback.NONE)))
        .filter(sent__gte=2, clicks=0, replies=0)
        .order_by("-sent")[:TOP]
    )

    issues_sent = NewsletterIssue.objects.filter(sent_at__isnull=False).count()
    readers_who_clicked = LinkClick.objects.values("reader").distinct().count()

    return render(request, "desk/insights.html", {
        "page_title": "Insights",
        "page_blurb": "What readers told us they want, and what they actually opened. "
                      "The gap between the two is where the editing happens.",
        "breadcrumbs": [("Insights", None)],
        "readers_total": readers_total,
        "issues_sent": issues_sent,
        "recs_total": recs_total,
        "responded": responded,
        "response_rate": _percent(responded, recs_total),
        "clicks_total": clicks_total,
        "click_rate": _percent(booking_clicks, recs_total),
        "readers_who_clicked": readers_who_clicked,
        "reach": _percent(readers_who_clicked, readers_total),
        "feedback": feedback,
        "top_interests": top_interests,
        "unmet": unmet,
        "category_demand": category_demand,
        "sections": sections,
        "most_clicked": most_clicked,
        "ignored": ignored,
        "campaigns_sent": Campaign.objects.filter(status=Campaign.Status.SENT).count(),
    })
