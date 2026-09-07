"""
Django settings for the The Ether project.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-o28%^&kgm55he4kj(m1l_h&71i^gdfzr+0%0werj5=9_iu5bpe",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o
]

# Render sets this to the service's *.onrender.com hostname automatically -
# trust it for both host and CSRF checks without needing a manual env var.
RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

# Render terminates TLS at the edge and forwards the original scheme in this
# header - without it Django can't tell the request was actually HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not DEBUG
# The platform's own health check talks plain HTTP to the container, so the
# HTTPS redirect would answer it with a 301 and it would never see the 200.
SECURE_REDIRECT_EXEMPT = [r"^healthz/?$"]


INSTALLED_APPS = [
    "config.apps.DigestAdminConfig",  # branded admin site, replaces django.contrib.admin
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "siteconfig",
    "readers",
    "opportunities",
    "recommendations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "siteconfig.context_processors.site_config",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# Falls back to local SQLite when DATABASE_URL isn't set (local dev);
# Render provides DATABASE_URL pointing at the managed Postgres instance.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- The Ether specific settings -------------------------------------

# Base URL used to build absolute links (feedback links, booking links,
# unsubscribe links) inside outgoing emails. Set to the real domain in
# production, e.g. https://digest.example.com
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://127.0.0.1:8000")

# SendGrid is used to send the newsletter emails. Unset = dry-run mode.
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")

# The "From" address newsletters are sent from. Must be a verified sender
# (single sender or authenticated domain) in SendGrid before real sending
# will work - SendGrid rejects unverified senders with a 403.
EMAIL_FROM = os.environ.get("EMAIL_FROM", "The Ether <digest@example.com>")

# AI assistance (research, classification, matching, writing). Everything
# degrades to the deterministic path when this is unset, so the app runs
# fine without it - see recommendations/ai.py.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

# Per-call ceiling, and a total allowance for one newsletter send. Sends can
# be triggered from the admin, i.e. inside a web request, and a request that
# outlives gunicorn's timeout gets its worker killed - taking out every other
# request on that worker, not just the send. Anything past the budget falls
# back to the deterministic template.
OPENAI_TIMEOUT_SECONDS = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "8"))
AI_SEND_BUDGET_SECONDS = float(os.environ.get("AI_SEND_BUDGET_SECONDS", "15"))

# Max number of recommendations included in a single newsletter send.
RECOMMENDATIONS_PER_SEND = int(os.environ.get("RECOMMENDATIONS_PER_SEND", "4"))

# Don't recommend the same opportunity to a reader twice within this window.
RECOMMENDATION_COOLDOWN_DAYS = int(os.environ.get("RECOMMENDATION_COOLDOWN_DAYS", "60"))
