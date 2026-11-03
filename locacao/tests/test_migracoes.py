from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MarcarImportadasTests(TransactionTestCase):
    def migrar(self, alvo):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("locacao", alvo)])
        return executor.loader.project_state([("locacao", alvo)]).apps

    def tearDown(self):
        self.migrar("0005_locacao_editada")

    def test_marca_locacao_com_mes_anterior_ao_limite(self):
        apps = self.migrar("0003_config_gerar_a_partir_de")
        Config, Imovel, Inquilino, Locacao, Cobranca = (
            apps.get_model("locacao", m) for m in ("Config", "Imovel", "Inquilino", "Locacao", "Cobranca"))
        Config.objects.create(pk=1, gerar_a_partir_de=date(2026, 11, 1))
        inq = Inquilino.objects.create(nome="Inquilino Teste")
        locs = []
        for i, comp in enumerate((date(2026, 5, 1), date(2026, 11, 1))):
            loc = Locacao.objects.create(imovel=Imovel.objects.create(codigo=f"UF-00{i}"), inquilino=inq,
                                         inicio=date(2026, 5, 1), dia_vencimento=10, indice="NENHUM")
            Cobranca.objects.create(locacao=loc, competencia=comp, vencimento=comp, valor_esperado=100)
            locs.append(loc.pk)
        apps = self.migrar("0004_importado")
        Locacao = apps.get_model("locacao", "Locacao")
        self.assertEqual([Locacao.objects.get(pk=pk).importada for pk in locs], [True, False])


class SqliteTests(TransactionTestCase):
    def test_arquivo_abre_em_wal_com_timeout_e_immediate(self):
        import os
        import tempfile

        from django.db import connections
        from django.db.backends.sqlite3.base import DatabaseWrapper
        fd, caminho = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.addCleanup(os.remove, caminho)
        self.addCleanup(lambda: [os.remove(caminho + s) for s in ("-wal", "-shm") if os.path.exists(caminho + s)])
        w = DatabaseWrapper({**connections["default"].settings_dict, "NAME": caminho})
        self.addCleanup(w.close)
        with w.cursor() as c:
            c.execute("PRAGMA journal_mode")
            self.assertEqual(c.fetchone()[0], "wal")
        opcoes = connections["default"].settings_dict["OPTIONS"]
        self.assertEqual((opcoes["timeout"], opcoes["transaction_mode"]), (20, "IMMEDIATE"))
