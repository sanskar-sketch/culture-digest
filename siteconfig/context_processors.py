from .models import SiteConfig


def site_config(request):
    """Makes the editable branding available to every template."""
    return {"site_config": SiteConfig.load()}
