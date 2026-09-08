"""The desk's sidebar: which row is active, given the current URL."""

from django.urls import NoReverseMatch, reverse

from config.dashboard import SECTIONS


def sidebar_sections(request):
    path = request.path
    sections = []
    for section in SECTIONS:
        rows = []
        for row in section["rows"]:
            if row.get("external_admin_url_name"):
                url = reverse(row["external_admin_url_name"])
                active = False
                add_url = None
            else:
                try:
                    url = reverse(f"desk:{row['key']}_list")
                except NoReverseMatch:
                    url = reverse(f"desk:{row['key']}")
                active = path == url or path.startswith(url.rstrip("/") + "/")
                try:
                    add_url = reverse(f"desk:{row['key']}_add")
                except NoReverseMatch:
                    add_url = None
            rows.append({**row, "url": url, "active": active, "add_url": add_url})
        sections.append({**section, "rows": rows})
    return sections
