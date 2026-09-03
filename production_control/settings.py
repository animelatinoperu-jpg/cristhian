from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parent.parent

_env_file = BASE_DIR / ".env"
if _env_file.is_file():
    with open(_env_file, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _val = _line.partition("=")
            _key = _key.strip()
            _val = _val.strip().strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val


def env(name, default=None):
    return os.environ.get(name, default)


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = True  # TEMP: forzado para diagnosticar 500 en productions:list tras login OAuth
ALLOWED_HOSTS = [item.strip() for item in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,*.up.railway.app,*.railway.app").split(",") if item.strip()]
CSRF_TRUSTED_ORIGINS = [item.strip() for item in env("CSRF_TRUSTED_ORIGINS", "https://*.up.railway.app,https://*.railway.app").split(",") if item.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "productions",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "productions.middleware.AuditRequestMiddleware",
]
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "production_control.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "productions.context_processors.navigation_permissions",
            ]
        },
    }
]
WSGI_APPLICATION = "production_control.wsgi.application"
ASGI_APPLICATION = "production_control.asgi.application"

if env("DB_ENGINE", "sqlite") == "postgresql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB") or env("PGDATABASE", "production_control"),
            "USER": env("POSTGRES_USER") or env("PGUSER", "production_user"),
            "PASSWORD": env("POSTGRES_PASSWORD") or env("PGPASSWORD", ""),
            "HOST": env("POSTGRES_HOST") or env("PGHOST", "db"),
            "PORT": env("POSTGRES_PORT") or env("PGPORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    sqlite_path = env("SQLITE_PATH", "").strip()
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(sqlite_path) if sqlite_path else BASE_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 30},
        }
    }

_railway_env = {}
_railway_file = BASE_DIR / ".env.railway"
if _railway_file.is_file():
    with open(_railway_file, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _val = _line.partition("=")
            _key = _key.strip()
            _val = _val.strip().strip('"').strip("'")
            if _key:
                _railway_env[_key] = _val
if _railway_env:
    DATABASES["railway"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _railway_env.get("POSTGRES_DB", "railway"),
        "USER": _railway_env.get("POSTGRES_USER", "postgres"),
        "PASSWORD": _railway_env.get("POSTGRES_PASSWORD", ""),
        "HOST": _railway_env.get("POSTGRES_HOST", "localhost"),
        "PORT": _railway_env.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"sslmode": "require"},
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-pe"
TIME_ZONE = "America/Lima"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"
    },
}
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", str(BASE_DIR / "storage" / "private")))
MEDIA_URL = "/private-files-not-served/"
LOGIN_REDIRECT_URL = "productions:list"
LOGOUT_REDIRECT_URL = "login"
LOGIN_URL = "login"
AUTH_USER_MODEL = "productions.User"
AUTHENTICATION_BACKENDS = [
    "productions.auth_backends.LockoutBackend",
]
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", "642670278151-pgkmlqd5ff9kdgkbvfnde5ipd62en84t.apps.googleusercontent.com")
GOOGLE_OAUTH_CLIENT_SECRET = env("GOOGLE_OAUTH_CLIENT_SECRET", "GOCSpX-wTAkHRrg26x7iDUKMfJoz_l8Fudd")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TEST_RUNNER = "productions.test_runner.TemporaryMediaTestRunner"
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
MAX_TEMPLATE_SIZE = int(env("MAX_TEMPLATE_SIZE", str(50 * 1024 * 1024)))
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
CSRF_FAILURE_VIEW = "productions.views.csrf_failure"
SESSION_COOKIE_SECURE = env("SECURE_COOKIES", "0") == "1"
CSRF_COOKIE_SECURE = env("SECURE_COOKIES", "0") == "1"
SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT", "0") == "1"
SECURE_REDIRECT_EXEMPT = [r"^salud/$"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env("SECURE_HSTS_INCLUDE_SUBDOMAINS", "0") == "1"
SECURE_HSTS_PRELOAD = env("SECURE_HSTS_PRELOAD", "0") == "1"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
LOGIN_FAILURE_LIMIT = int(env("LOGIN_FAILURE_LIMIT", "5"))
LOGIN_LOCK_MINUTES = int(env("LOGIN_LOCK_MINUTES", "15"))

PRIVATE_TEMPLATE_DIR = MEDIA_ROOT / "templates"
PRIVATE_GENERATED_DIR = MEDIA_ROOT / "generated"
EXCEL_MAPPING_PATH = BASE_DIR / "config" / "excel_mapping_v1.yaml"
TEMPLATE_SOURCE_PATH = BASE_DIR / "input" / "PLANTILLA_PP_V1.xlsm"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
        "file": {"class": "logging.handlers.RotatingFileHandler", "filename": BASE_DIR / "production.log", "maxBytes": 5_000_000, "backupCount": 5},
    },
    "root": {"handlers": ["console", "file"], "level": env("LOG_LEVEL", "INFO")},
}
