from datetime import date
from decimal import Decimal

from django.test import TestCase

from locacao.apuracao import resumo_mes, resumo_periodo, totais
from locacao.cobrancas import gerar_cobrancas, registrar_pagamento
from locacao.models import Cobranca, Config, Empresa, Imovel, Inquilino, Locacao, ValorAluguel

SET, OUT, NOV = date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)
HOJE = date(2026, 11, 2)


class ApuracaoTests(TestCase):
    def setUp(self):
        self.inquilino = Inquilino.objects.create(nome="Inquilino Teste", documento="000.000.000-00")
        self.n = 0

    def corte(self, mes):
        c = Config.get()
        c.mes_corte = mes
        c.save()

    def nova_locacao(self, valor="100", inicio=SET, acesso=True, participacao="100"):
        self.n += 1
        empresa = Empresa.objects.create(nome=f"Empresa Teste {self.n}", acesso_direto=acesso)
        imovel = Imovel.objects.create(codigo=f"UF-00{self.n}", empresa=empresa, participacao_pct=Decimal(participacao))
        loc = Locacao.objects.create(imovel=imovel, inquilino=self.inquilino, inicio=inicio, dia_vencimento=10, indice="IGPM")
        ValorAluguel.objects.create(locacao=loc, vigente_desde=inicio, valor=Decimal(valor), motivo="INICIAL")
        gerar_cobrancas(inicio, loc)
        return loc

    def test_esperada_valor_cheio_com_desconto(self):
        loc = self.nova_locacao()
        registrar_pagamento(loc, date(2026, 9, 8), Decimal("90"))
        cob = loc.cobrancas.get()
        cob.quitada = True
        cob.save()
        r = resumo_mes(SET, HOJE)
        self.assertEqual((r.esperada, r.realizada, r.pendente_total), (Decimal("100"), Decimal("90"), Decimal("0")))
        self.assertEqual(len(r.recebidos), 1)
        self.assertEqual(r.pendentes, [])

    def test_pendente_separado(self):
        self.nova_locacao(acesso=False)
        self.nova_locacao(valor="50", acesso=True)
        r = resumo_mes(SET, HOJE)
        self.assertEqual(r.pendente_aguardando, Decimal("100"))
        self.assertEqual(r.pendente_atrasado, Decimal("50"))
        self.assertEqual(r.pendente_total, Decimal("150"))
        self.assertEqual([l.cobranca.locacao.imovel.codigo for l in r.pendentes], ["UF-001", "UF-002"])

    def test_parcial_vencida_sem_acesso_fica_aguardando(self):
        loc = self.nova_locacao(acesso=False)
        registrar_pagamento(loc, date(2026, 9, 8), Decimal("40"))
        r = resumo_mes(SET, HOJE)
        self.assertEqual((r.pendente_aguardando, r.pendente_atrasado), (Decimal("60"), Decimal("0")))
        r = resumo_mes(SET, date(2026, 9, 9))  # antes do vencimento: em aberto
        self.assertEqual((r.pendente_aguardando, r.pendente_atrasado), (Decimal("0"), Decimal("60")))

    def test_regra_antiga_usa_comissao_importada(self):
        loc = self.nova_locacao()
        registrar_pagamento(loc, date(2026, 9, 8), Decimal("100"))
        loc.cobrancas.update(comissao_importada=Decimal("12.34"))
        self.assertEqual(resumo_mes(SET, HOJE).comissao, Decimal("12.34"))

    def test_regra_antiga_participacao(self):
        loc = self.nova_locacao(valor="1000", participacao="80")
        registrar_pagamento(loc, date(2026, 9, 8), Decimal("1000"))
        r = resumo_mes(SET, HOJE)
        self.assertFalse(r.regra_nova)
        self.assertEqual(r.comissao, Decimal("40.00"))

    def test_regra_nova_atraso_cai_no_mes_do_pagamento(self):
        self.corte(SET)
        loc = self.nova_locacao()
        p = registrar_pagamento(loc, date(2026, 10, 3), Decimal("100"))
        s, o = resumo_mes(SET, HOJE), resumo_mes(OUT, HOJE)
        self.assertTrue(s.regra_nova)
        self.assertEqual((s.comissao, s.realizada), (Decimal("0"), Decimal("0")))
        self.assertEqual((o.comissao, o.realizada), (Decimal("5.00"), Decimal("100")))
        self.assertEqual(o.pagamentos[0].pagamento, p)
        self.assertEqual(o.pagamentos[0].competencias, [SET])

    def test_regra_nova_antecipacao_inteira(self):
        self.corte(SET)
        loc = self.nova_locacao()
        registrar_pagamento(loc, date(2026, 9, 1), Decimal("600"))
        s, o = resumo_mes(SET, HOJE), resumo_mes(OUT, HOJE)
        self.assertEqual((s.realizada, s.comissao), (Decimal("600"), Decimal("30.00")))
        self.assertEqual(len(s.pagamentos[0].competencias), 6)
        self.assertEqual(o.comissao, Decimal("0"))

    def test_regra_nova_pagamento_sem_data_cai_na_competencia(self):
        self.corte(SET)
        loc = self.nova_locacao()
        registrar_pagamento(loc, None, Decimal("100"))
        r = resumo_mes(SET, HOJE)
        self.assertEqual((r.realizada, r.comissao, r.pagamentos), (Decimal("100"), Decimal("5.00"), []))

    def test_cada_alocacao_conta_uma_vez_em_volta_do_corte(self):
        self.corte(OUT)
        loc = self.nova_locacao(inicio=SET)
        gerar_cobrancas(NOV, loc)
        set_, out, nov = loc.cobrancas.all()
        registrar_pagamento(loc, date(2026, 10, 3), Decimal("100"), alocacoes=[(set_, Decimal("100"))])  # data
        registrar_pagamento(loc, None, Decimal("100"), alocacoes=[(out, Decimal("100"))])  # sem data
        registrar_pagamento(loc, date(2026, 9, 20), Decimal("100"), alocacoes=[(nov, Decimal("100"))])  # antes do corte
        s, o, n = (resumo_mes(m, HOJE) for m in (SET, OUT, NOV))
        self.assertEqual([r.realizada for r in (s, o, n)], [Decimal("0"), Decimal("200"), Decimal("100")])
        t = totais([s, o, n])
        self.assertEqual((t["realizada"], t["comissao"]), (Decimal("300"), Decimal("15.00")))

    def test_corte_nulo_regra_antiga(self):
        self.corte(None)
        loc = self.nova_locacao()
        registrar_pagamento(loc, date(2026, 10, 3), Decimal("100"))
        s, o = resumo_mes(SET, HOJE), resumo_mes(OUT, HOJE)
        self.assertFalse(s.regra_nova)
        self.assertEqual((s.realizada, s.comissao), (Decimal("100"), Decimal("5.00")))
        self.assertEqual(o.comissao, Decimal("0"))

    def test_corte_mes_anterior_usa_regra_antiga(self):
        self.corte(OUT)
        loc = self.nova_locacao()
        registrar_pagamento(loc, date(2026, 10, 3), Decimal("100"))
        self.assertFalse(resumo_mes(SET, HOJE).regra_nova)
        self.assertEqual(resumo_mes(SET, HOJE).comissao, Decimal("0"))  # pago depois do corte: cai em outubro
        self.assertEqual(resumo_mes(OUT, HOJE).comissao, Decimal("5.00"))

    def test_corte_nao_conta_duas_vezes(self):
        dez = date(2026, 12, 1)
        self.corte(dez)
        loc = self.nova_locacao(valor="1000", inicio=NOV)
        registrar_pagamento(loc, date(2026, 12, 3), Decimal("1000"))  # novembro pago em dezembro
        hoje = date(2027, 1, 5)
        n, d = resumo_mes(NOV, hoje), resumo_mes(dez, hoje)
        self.assertEqual((n.realizada, n.comissao), (Decimal("0"), Decimal("0")))
        self.assertEqual((d.realizada, d.comissao), (Decimal("1000"), Decimal("50.00")))
        t = totais(resumo_periodo(NOV, dez, hoje))
        self.assertEqual((t["realizada"], t["comissao"]), (Decimal("1000"), Decimal("50.00")))

    def test_corte_mantem_pagamento_antes_do_corte_e_sem_data(self):
        self.corte(OUT)
        loc = self.nova_locacao()
        registrar_pagamento(loc, date(2026, 9, 8), Decimal("60"))
        registrar_pagamento(loc, None, Decimal("40"))
        s = resumo_mes(SET, HOJE)
        self.assertEqual((s.realizada, s.comissao), (Decimal("100"), Decimal("5.00")))

    def test_periodo_e_totais(self):
        loc = self.nova_locacao()
        gerar_cobrancas(NOV, loc)
        registrar_pagamento(loc, date(2026, 9, 8), Decimal("200"))
        resumos = resumo_periodo(SET, date(2026, 11, 20), HOJE)
        self.assertEqual([r.mes for r in resumos], [SET, OUT, NOV])
        self.assertEqual(totais(resumos), {
            "esperada": Decimal("300"), "realizada": Decimal("200"),
            "pendente_total": Decimal("100"), "comissao": Decimal("10.00"),
        })

    def test_consultas_nao_crescem_com_imoveis(self):
        for _ in range(5):
            self.nova_locacao()
        Config.get()
        with self.assertNumQueries(3):  # config, cobranças, alocações
            resumo_mes(SET, HOJE)

    def test_consultas_regra_nova_nao_crescem(self):
        self.corte(SET)
        for _ in range(4):
            loc = self.nova_locacao()
            registrar_pagamento(loc, date(2026, 9, 5), Decimal("60"))
            registrar_pagamento(loc, date(2026, 9, 9), Decimal("40"))
        Config.get()
        with self.assertNumQueries(6):  # config, cobranças, alocações, pagamentos, alocações dos pagamentos, cobranças delas
            r = resumo_mes(SET, HOJE)
        self.assertEqual((len(r.pagamentos), r.realizada), (8, Decimal("400")))

    def test_alocacao_manual_com_prefetch_quita(self):
        loc = self.nova_locacao()
        cob = Cobranca.objects.prefetch_related("alocacoes").get(locacao=loc)
        registrar_pagamento(loc, date(2026, 9, 8), Decimal("100"), alocacoes=[(cob, Decimal("100"))])
        cob.refresh_from_db()
        self.assertTrue(cob.quitada)
