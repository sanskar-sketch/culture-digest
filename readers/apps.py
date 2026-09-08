import os

from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _bootstrap_superuser(sender, **kwargs):
    """Create an admin login from env vars if one doesn't exist yet.

    Render's free-tier MCP tooling has no interactive shell access, so this
    runs as part of `migrate` (already in the build command) instead -
    idempotent, and re-runs harmlessly on every deploy including after a
    database switch (e.g. Render Postgres -> Supabase).
    """
    username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
    email = os.environ.get("DJANGO_SUPERUSER_EMAIL")
    password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
    if not (username and password):
        return

    from django.contrib.auth import get_user_model

    User = get_user_model()
    if User.objects.filter(username=username).exists():
        return
    User.objects.create_superuser(username=username, email=email or "", password=password)


class ReadersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'readers'

    verbose_name = "Readers"
    def ready(self):
        post_migrate.connect(_bootstrap_superuser, sender=self)
