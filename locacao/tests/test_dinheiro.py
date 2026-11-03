from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from locacao.dinheiro import comissao, fator_acumulado, primeiro_dia, somar_meses


class DinheiroTests(SimpleTestCase):
    def test_comissao_com_participacao(self):
        self.assertEqual(comissao(Decimal("10000"), Decimal("80"), Decimal("5")), Decimal("400.00"))

    def test_comissao_arredonda_meio_para_cima(self):
        self.assertEqual(comissao(Decimal("1234.50"), Decimal("100"), Decimal("5")), Decimal("61.73"))

    def test_fator_acumulado_multiplica(self):
        self.assertEqual(fator_acumulado([Decimal("1"), Decimal("1")]), Decimal("1.0201"))
        self.assertEqual(fator_acumulado([Decimal("-0.5"), Decimal("0.5")]), Decimal("0.999975"))

    def test_meses(self):
        self.assertEqual(somar_meses(date(2026, 11, 1), 3), date(2027, 2, 1))
        self.assertEqual(somar_meses(date(2026, 3, 31), -1), date(2026, 2, 1))
        self.assertEqual(primeiro_dia(date(2026, 8, 17)), date(2026, 8, 1))
