from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _wal(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        with connection.cursor() as c:
            c.execute("PRAGMA journal_mode=WAL")  # em memória (testes) continua "memory"


class LocacaoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "locacao"
    verbose_name = "Locação"

    def ready(self):
        connection_created.connect(_wal)
