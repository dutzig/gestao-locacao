import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
# Sem chave no ambiente só é aceito em desenvolvimento e nos testes.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
TESTANDO = sys.argv[1:2] == ["test"]
if not SECRET_KEY:
    if not (DEBUG or TESTANDO):
        raise RuntimeError("Defina DJANGO_SECRET_KEY (ou DJANGO_DEBUG=1 em desenvolvimento).")
    SECRET_KEY = "dev-inseguro-nao-usar-em-producao"

APP_HOST = os.environ.get("APP_HOST", "")
ALLOWED_HOSTS = [h for h in (APP_HOST, "localhost", "127.0.0.1") if h]
if APP_HOST:
    CSRF_TRUSTED_ORIGINS = ["https://" + APP_HOST]

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "locacao",
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
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DB_PATH", BASE_DIR / "db.sqlite3"),
        # Espera o lock em vez de "database is locked"; IMMEDIATE pega o lock de escrita no início da transação.
        # WAL (leitura não bloqueia escrita) é ligado em locacao.apps.
        "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Existe antes do collectstatic para o WhiteNoise não avisar em dev/testes.
STATIC_ROOT.mkdir(exist_ok=True)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Produção: nomes com hash (cache longo sem servir CSS velho); dev/testes dispensam o collectstatic.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage" if DEBUG or TESTANDO
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "inicio"
LOGOUT_REDIRECT_URL = "login"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
