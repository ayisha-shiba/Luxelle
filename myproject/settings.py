from pathlib import Path
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env into os.environ. override=True so the .env file is the source of
# truth in dev, even if a stale variable lingers in the shell session.
# (In production there's no .env, so real environment variables are used.)
load_dotenv(BASE_DIR / ".env", override=True)

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-CHANGE-THIS-IN-PRODUCTION",
)

DEBUG = os.environ.get("DEBUG", "True").lower() == "true"

ALLOWED_HOSTS = os.environ.get(
    "ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
).split(",")

 
# Application definition


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "core",
    "payments",
    "wallet",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.NoCacheForAuthMiddleware",  
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "myproject.urls"

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
                "core.context_processors.cart_wishlist_counts",
            ],
        },
    },
]

WSGI_APPLICATION = "myproject.wsgi.application"



# Auth & Allauth

AUTH_USER_MODEL = "core.CustomUser"
SITE_ID = int(os.environ.get("SITE_ID", 1))

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_REDIRECT_URL          = "/home/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"

ACCOUNT_LOGIN_METHODS              = {"email"}
ACCOUNT_SIGNUP_FIELDS              = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD  = None
ACCOUNT_EMAIL_VERIFICATION         = "optional"
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 3
ACCOUNT_SESSION_REMEMBER           = True
ACCOUNT_LOGOUT_ON_GET              = True
ACCOUNT_ALLOW_REGISTRATION         = True

SOCIALACCOUNT_AUTO_SIGNUP    = True
SOCIALACCOUNT_STORE_TOKENS   = True
SOCIALACCOUNT_QUERY_EMAIL    = True
SOCIALACCOUNT_LOGIN_ON_GET   = True
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE":             ["profile", "email"],
        "AUTH_PARAMS":       {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
    }
}
SOCIALACCOUNT_ADAPTER = "core.adapters.CustomSocialAccountAdapter"
ACCOUNT_ADAPTER       = "core.adapters.CustomAccountAdapter"



# Database

DATABASES = {
    "default": {
        "ENGINE":   "django.db.backends.postgresql",
        "NAME":     os.environ.get("DB_NAME", "luxelle_db"),
        "USER":     os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST":     os.environ.get("DB_HOST", "localhost"),
        "PORT":     os.environ.get("DB_PORT", "5432"),
    }
}


# Sessions

SESSION_ENGINE           = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE       = 1209600    
SESSION_COOKIE_HTTPONLY  = True        
SESSION_COOKIE_SAMESITE  = "Lax"      
SESSION_COOKIE_SECURE    = not DEBUG  
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False



# CSRF

CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE   = not DEBUG   
CSRF_TRUSTED_ORIGINS = [
    'http://localhost:8000', 
    'http://127.0.0.1:8000',
    'https://*.vscode.dev',
    'https://*.github.dev',
    'https://*.trycloudflare.com',
    'https://*.ngrok-free.app',
    'https://*.ngrok.io',
]

if os.environ.get("CSRF_TRUSTED_ORIGINS"):
    CSRF_TRUSTED_ORIGINS.extend(os.environ.get("CSRF_TRUSTED_ORIGINS").split(","))



# Password Validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalisation

LANGUAGE_CODE = "en-us"
TIME_ZONE     = "UTC"
USE_I18N      = True
USE_TZ        = True


# Static & Media

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT     = BASE_DIR / "staticfiles"   # For collectstatic in production

MEDIA_URL  = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Email (SMTP)

EMAIL_BACKEND       = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST          = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT          = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USE_TLS       = os.environ.get("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_HOST_USER     = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL  = EMAIL_HOST_USER

# Razorpay (test keys live in .env, never commit real keys)
RAZORPAY_KEY_ID     = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# Logging


LOGGING = {
    "version":                  1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style":  "{",
        },
    },
    "handlers": {
        "console": {
            "class":     "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level":    "INFO",
    },
    "loggers": {
        "django": {
            "handlers":  ["console"],
            "level":     "WARNING",
            "propagate": False,
        },
        "core": {
            "handlers":  ["console"],
            "level":     "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}

LOGIN_URL = 'login'
LOGOUT_REDIRECT_URL = 'login'

# Referral reward — amount (in rupees) credited to each wallet on a successful referral.
# Override by setting REFERRAL_REWARD_AMOUNT in .env (e.g. REFERRAL_REWARD_AMOUNT=200).
from decimal import Decimal as _Decimal
REFERRAL_REWARD_AMOUNT = _Decimal(os.environ.get("REFERRAL_REWARD_AMOUNT", "100"))
