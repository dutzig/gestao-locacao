from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from locacao.templatetags.br import brl, data_br, mes_br


class FiltrosBrTests(SimpleTestCase):
    def test_brl(self):
        self.assertEqual(brl(Decimal("1234.5")), "R$ 1.234,50")
        self.assertEqual(brl(Decimal("-10")), "-R$ 10,00")
        self.assertEqual(brl(None), "—")

    def test_brl_arredonda_meio_para_cima(self):
        self.assertEqual(brl(Decimal("0.005")), "R$ 0,01")
        self.assertEqual(brl(Decimal("1234567.891")), "R$ 1.234.567,89")

    def test_data_br(self):
        self.assertEqual(data_br(date(2026, 8, 1)), "01/08/2026")
        self.assertEqual(data_br(None), "—")

    def test_mes_br(self):
        self.assertEqual(mes_br(date(2026, 8, 1)), "ago/2026")
        self.assertEqual(mes_br(date(2026, 12, 31)), "dez/2026")


class TemplatesTests(SimpleTestCase):
    def test_todo_cabecalho_de_tabela_tem_scope(self):
        import re
        from pathlib import Path
        for arq in (Path(__file__).resolve().parent.parent / "templates").rglob("*.html"):
            for th in re.findall(r"<th(?!ead)[^>]*>", arq.read_text()):
                with self.subTest(arquivo=arq.name, th=th):
                    self.assertIn("scope=", th)

    def test_nome_mes(self):
        from locacao.templatetags.br import nome_mes
        self.assertEqual((nome_mes(3), nome_mes(12)), ("março", "dezembro"))
