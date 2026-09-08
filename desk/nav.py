"""The desk's sidebar: which row is active, given the current URL."""

from django.urls import NoReverseMatch, reverse

from config.dashboard import SECTIONS


def sidebar_sections(request):
    path = request.path
    sections = []
    for section in SECTIONS:
        # Account management is superuser-only, so don't advertise it to
        # someone who would only get a 403 (see desk.views.access).
        if section.get("superuser_only") and not request.user.is_superuser:
            continue
        rows = []
        for row in section["rows"]:
            try:
                url = reverse(f"desk:{row['key']}_list")
            except NoReverseMatch:
                url = reverse(f"desk:{row['key']}")
            active = path == url or path.startswith(url.rstrip("/") + "/")
            try:
                add_url = reverse(f"desk:{row['key']}_add")
            except NoReverseMatch:
                add_url = None
            rows.append({
                **row,
                "url": url,
                "active": active,
                "add_url": add_url,
                # "Add listing", not "Add listings".
                "add_label": f"Add {row.get('singular', row['name'].lower())}",
            })
        sections.append({**section, "rows": rows})
    return sections
