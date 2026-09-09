from django.shortcuts import render

from config import dashboard
from desk.permissions import staff_required


@staff_required
def dashboard_view(request):
    data = dashboard.stats()
    context = {
        "page_title": "Overview",
        "stats": data,
        # The health panel itself lives on Settings now; the Overview only
        # says whether anything there needs looking at.
        "problems": sum(1 for row in dashboard.configuration() if not row["ok"]),
    }
    return render(request, "desk/dashboard.html", context)
