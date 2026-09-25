def desk_context(request):
    """The sidebar for every desk page. Branding (site_config) is already
    global via siteconfig.context_processors. Guarded by path so this does
    no work on the public site or the old admin, which share this
    project's context processor list."""
    if not request.path.startswith("/desk/"):
        return {}

    from .nav import footer_links, sidebar_sections

    return {"sidebar_sections": sidebar_sections(request),
            "footer_links": footer_links(request),
            "link_warning": link_warning(request)}


def link_warning(request) -> str:
    """Say so when emails would link somewhere other than this site.

    Every button in an email - Details, the review links, More like this -
    is built from SITE_BASE_URL. If that names another address, readers
    click through to whatever answers there: locally, often another app
    on the same port, which says "page not found".
    """
    from urllib.parse import urlparse

    from django.conf import settings

    def same_machine(host: str) -> str:
        # localhost and 127.0.0.1 are one address; don't cry wolf over it.
        return host.replace("localhost", "127.0.0.1")

    base = getattr(settings, "SITE_BASE_URL", "") or ""
    linked = urlparse(base).netloc.lower()
    here = request.get_host().lower()
    if not linked or same_machine(linked) == same_machine(here):
        return ""
    scheme = "https" if request.is_secure() else "http"
    return (f"Emails sent from here link to {base}, but this desk is at {scheme}://{here}. "
            f"Buttons in those emails won't reach this site. Set SITE_BASE_URL to "
            f"{scheme}://{here} and restart.")
