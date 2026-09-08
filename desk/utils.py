from django.core.paginator import Paginator
from django.db.models import Q


def paginate(request, queryset, per_page=50):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


def search(queryset, request, fields):
    """?q= against an OR of icontains lookups across the given fields."""
    q = (request.GET.get("q") or "").strip()
    if not q:
        return queryset
    condition = Q()
    for field in fields:
        condition |= Q(**{f"{field}__icontains": q})
    return queryset.filter(condition).distinct()


def filter_options(request, param, choices, all_label="All"):
    """One filter group's option list: 'All' plus each (value, label) choice,
    with the current selection marked."""
    current = request.GET.get(param, "")
    options = [{"value": "", "label": all_label, "selected": not current}]
    for value, label in choices:
        options.append({"value": str(value), "label": str(label), "selected": current == str(value)})
    return options
