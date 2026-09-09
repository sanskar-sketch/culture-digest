from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def qs_with(context, **overrides):
    """The current query string with the given parameters replaced.

    Used everywhere a link has to keep the active search/filter/page state
    intact while changing one thing (the page number, one filter value).
    """
    request = context["request"]
    params = request.GET.copy()
    for key, value in overrides.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    # With nothing left, the link must still go somewhere. An empty href
    # means "this page as it is" - query string included - so the "All"
    # chip on a page with one filter reloaded that same filter and looked
    # dead. The bare path is the page with nothing applied.
    return f"?{encoded}" if encoded else request.path


@register.simple_tag(takes_context=True)
def qs_with_param(context, param, value):
    """Like qs_with, but the parameter name itself is a variable - needed
    for the filter panel, where each group's parameter name comes from
    data rather than being known in the template."""
    return qs_with(context, **{param: value, "page": None})


@register.filter
def field_type(field):
    return field.field.widget.__class__.__name__
