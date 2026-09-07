from django.contrib.admin.apps import AdminConfig


class DigestAdminConfig(AdminConfig):
    """Swaps in the branded admin site (see config.admin.DigestAdminSite)."""

    default_site = "config.admin.DigestAdminSite"
