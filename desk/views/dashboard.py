from django.shortcuts import render

from config import dashboard
from desk.permissions import staff_required


@staff_required
def dashboard_view(request):
    data = dashboard.stats()
    context = {
        "page_title": "Overview",
        "stats": data,
        "configuration": dashboard.configuration(),
    }
    return render(request, "desk/dashboard.html", context)
