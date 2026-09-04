"""
Django settings for the Culture Digest project.
"""

import os
from pathlib import Path

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


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "readers",
    "opportunities",
    "recommendations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Culture Digest specific settings -------------------------------------

# Base URL used to build absolute links (feedback links, booking links,
# unsubscribe links) inside outgoing emails. Set to the real domain in
# production, e.g. https://digest.example.com
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://127.0.0.1:8000")

# Resend (https://resend.com) is used to send the newsletter emails.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")

# The "From" address newsletters are sent from. Must be a verified sender
# domain in Resend before real sending will work.
NEWSLETTER_FROM_EMAIL = os.environ.get(
    "NEWSLETTER_FROM_EMAIL", "Culture Digest <digest@example.com>"
)

# Max number of recommendations included in a single newsletter send.
RECOMMENDATIONS_PER_SEND = int(os.environ.get("RECOMMENDATIONS_PER_SEND", "4"))

# Don't recommend the same opportunity to a reader twice within this window.
RECOMMENDATION_COOLDOWN_DAYS = int(os.environ.get("RECOMMENDATION_COOLDOWN_DAYS", "60"))
