from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from desk.permissions import staff_required
from desk.utils import filter_options, paginate, search
from opportunities.models import Category
from recommendations.models import NewsletterIssue, Recommendation


@staff_required
def issue_list(request):
    qs = NewsletterIssue.objects.select_related("reader").annotate(
        recs_count=Count("recommendations", distinct=True),
        replied_count=Count("recommendations", filter=~Q(recommendations__feedback=Recommendation.Feedback.NONE), distinct=True),
    )
    qs = search(qs, request, ["reader__email", "reader__name", "recommendations__opportunity__title"])
    delivery = request.GET.get("delivery")
    if delivery == "sent":
        qs = qs.filter(sent_at__isnull=False)
    elif delivery == "unsent":
        qs = qs.filter(sent_at__isnull=True)

    page_obj = paginate(request, qs.order_by("-created_at"))
    context = {
        "page_title": "Newsletter issues",
        "page_blurb": "One row per newsletter sent to one reader, with the picks it contained.",
        "breadcrumbs": [("Newsletter issues", None)],
        "page_obj": page_obj,
        "result_count": qs.count(),
        "search_placeholder": "Search by reader or listing…",
        "filter_groups": [
            {"title": "Delivery", "param": "delivery", "options": filter_options(request, "delivery", [("sent", "Sent"), ("unsent", "Not sent")])},
        ],
        "has_active_filters": bool(request.GET.get("delivery")),
    }
    return render(request, "desk/issue_list.html", context)


@staff_required
def issue_detail(request, pk):
    issue = get_object_or_404(NewsletterIssue.objects.select_related("reader"), pk=pk)
    recs = issue.recommendations.select_related("opportunity").order_by("-created_at")
    context = {
        "page_title": f"Issue #{issue.pk}",
        "breadcrumbs": [("Newsletter issues", reverse("desk:issues_list")), (f"Issue #{issue.pk}", None)],
        "issue": issue,
        "recommendations": recs,
    }
    return render(request, "desk/issue_detail.html", context)


@staff_required
def recommendation_list(request):
    qs = Recommendation.objects.select_related("opportunity", "issue__reader")
    qs = search(qs, request, ["opportunity__title", "issue__reader__email", "rationale"])
    feedback = request.GET.get("feedback")
    if feedback:
        qs = qs.filter(feedback=feedback)
    category = request.GET.get("category")
    if category:
        qs = qs.filter(opportunity__category=category)

    page_obj = paginate(request, qs.order_by("-created_at"))
    context = {
        "page_title": "Recommendations",
        "page_blurb": "Every individual pick ever made, with the reader's verdict on it. "
                      "Where you see what lands and what doesn't.",
        "breadcrumbs": [("Recommendations", None)],
        "page_obj": page_obj,
        "result_count": qs.count(),
        "search_placeholder": "Search by listing, reader or rationale…",
        "filter_groups": [
            {"title": "Feedback", "param": "feedback", "options": filter_options(request, "feedback", Recommendation.Feedback.choices)},
            {"title": "Category", "param": "category", "options": filter_options(request, "category", Category.choices)},
        ],
        "has_active_filters": bool(request.GET.get("feedback") or request.GET.get("category")),
    }
    return render(request, "desk/recommendation_list.html", context)
