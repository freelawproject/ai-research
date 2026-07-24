"""Core Django settings.

The viewer runs WITHOUT a database: the JSON artifacts under DATA_ROOT are
the source of truth, and the app only indexes them in memory. Production
persistence is intentionally out of scope for this package — design it from
the artifact schema in docs/pipeline.md.
"""

from pathlib import Path

import environ

env = environ.FileAwareEnv()

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = env("SECRET_KEY", default="dev-only-insecure-key")
DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0"]
)

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "viewer",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "extraction.urls"
ASGI_APPLICATION = "extraction.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]

# No database — see module docstring.
DATABASES: dict = {}

STATIC_URL = "static/"

USE_TZ = True
