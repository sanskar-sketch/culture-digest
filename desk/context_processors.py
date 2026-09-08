def desk_context(request):
    """The sidebar for every desk page. Branding (site_config) is already
    global via siteconfig.context_processors. Guarded by path so this does
    no work on the public site or the old admin, which share this
    project's context processor list."""
    if not request.path.startswith("/desk/"):
        return {}

    from .nav import sidebar_sections

    return {"sidebar_sections": sidebar_sections(request)}
