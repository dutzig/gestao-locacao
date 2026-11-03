from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from locacao.models import (
    Alocacao, Cobranca, Config, Empresa, Imovel, Inquilino, Locacao, Pagamento, ValorAluguel,
)


class ModelosTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Teste")
        self.imovel = Imovel.objects.create(codigo="UF-001", empresa=self.empresa)
        self.inquilino = Inquilino.objects.create(nome="Inquilino Teste", documento="000.000.000-00")

    def nova_locacao(self, **kw):
        return Locacao.objects.create(
            imovel=self.imovel, inquilino=self.inquilino, inicio=date(2026, 1, 1),
            dia_vencimento=10, indice="IGPM", **kw,
        )

    def test_config_get_cria_singleton(self):
        c = Config.get()
        self.assertEqual(c.pk, 1)
        self.assertEqual(c.percentual_comissao, Decimal("5.00"))
        self.assertEqual(c.inicio_historico, date(2026, 2, 1))
        self.assertIsNone(c.mes_corte)
        self.assertEqual(Config.get().pk, 1)
        self.assertEqual(Config.objects.count(), 1)

    def test_valor_em_com_escalonamento_futuro(self):
        loc = self.nova_locacao()
        ValorAluguel.objects.create(locacao=loc, vigente_desde=date(2026, 1, 1), valor=Decimal("100"), motivo="INICIAL")
        ValorAluguel.objects.create(locacao=loc, vigente_desde=date(2027, 1, 1), valor=Decimal("110"), motivo="ESCALONAMENTO")
        self.assertEqual(loc.valor_em(date(2026, 12, 15)), Decimal("100"))
        self.assertEqual(loc.valor_em(date(2027, 1, 1)), Decimal("110"))
        self.assertIsNone(loc.valor_em(date(2025, 12, 31)))

    def test_imovel_vago(self):
        self.assertTrue(self.imovel.vago)
        self.assertIsNone(self.imovel.locacao_ativa)
        loc = self.nova_locacao()
        self.assertFalse(self.imovel.vago)
        self.assertEqual(self.imovel.locacao_ativa, loc)
        loc.ativa = False
        loc.save()
        self.assertTrue(self.imovel.vago)

    def test_cobranca_recebido_e_pagamento_nao_alocado(self):
        loc = self.nova_locacao()
        cob = Cobranca.objects.create(
            locacao=loc, competencia=date(2026, 2, 1), vencimento=date(2026, 2, 10), valor_esperado=Decimal("100"),
        )
        self.assertEqual(cob.recebido, Decimal("0"))
        p1 = Pagamento.objects.create(locacao=loc, data=date(2026, 2, 5), valor=Decimal("80"))
        p2 = Pagamento.objects.create(locacao=loc, data=date(2026, 2, 20), valor=Decimal("50"))
        Alocacao.objects.create(pagamento=p1, cobranca=cob, valor=Decimal("60"))
        Alocacao.objects.create(pagamento=p2, cobranca=cob, valor=Decimal("40"))
        self.assertEqual(cob.recebido, Decimal("100"))
        self.assertEqual(p1.nao_alocado, Decimal("20"))
        self.assertEqual(p2.nao_alocado, Decimal("10"))

    def test_centavos_somados_exatos(self):
        loc = self.nova_locacao()
        cob = Cobranca.objects.create(
            locacao=loc, competencia=date(2026, 2, 1), vencimento=date(2026, 2, 10), valor_esperado=Decimal("0.30"),
        )
        pag = Pagamento.objects.create(locacao=loc, data=date(2026, 2, 5), valor=Decimal("0.31"))
        Alocacao.objects.create(pagamento=pag, cobranca=cob, valor=Decimal("0.10"))
        Alocacao.objects.create(pagamento=pag, cobranca=cob, valor=Decimal("0.20"))
        self.assertEqual(cob.recebido, Decimal("0.30"))
        self.assertEqual(pag.nao_alocado, Decimal("0.01"))

    def test_banco_rejeita_dias_e_mes_invalidos(self):
        for campos in ({"dia_vencimento": 0}, {"dia_vencimento": 32}, {"dia_limite_desconto": 0},
                       {"mes_base_reajuste": 13}, {"mes_base_reajuste": 0}):
            dados = {"dia_vencimento": 10, **campos}
            with self.subTest(campos), self.assertRaises(IntegrityError), transaction.atomic():
                Locacao.objects.create(
                    imovel=self.imovel, inquilino=self.inquilino, inicio=date(2026, 1, 1), indice="IGPM", **dados,
                )
        self.nova_locacao(dia_limite_desconto=None, mes_base_reajuste=None)
