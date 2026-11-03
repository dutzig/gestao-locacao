from datetime import date
from decimal import Decimal

from django.test import TestCase

from locacao.cobrancas import (
    _criar_cobranca,
    encerrar_locacao, excluir_pagamento, excluir_valor, gerar_cobrancas, registrar_pagamento, saldo, situacao,
    vencimento_de,
)
from locacao.models import Cobranca, Config, Empresa, Imovel, Inquilino, Locacao, ValorAluguel


class CobrancasTests(TestCase):
    def setUp(self):
        c = Config.get()
        c.inicio_historico = date(2026, 2, 1)
        c.save()
        self.empresa = Empresa.objects.create(nome="Empresa Teste", acesso_direto=False)
        self.imovel = Imovel.objects.create(codigo="UF-001", empresa=self.empresa)
        self.inquilino = Inquilino.objects.create(nome="Inquilino Teste", documento="000.000.000-00")

    def nova_locacao(self, inicio=date(2026, 1, 1), valor="100", **kw):
        loc = Locacao.objects.create(
            imovel=self.imovel, inquilino=self.inquilino, inicio=inicio, dia_vencimento=10, indice="IGPM", **kw,
        )
        ValorAluguel.objects.create(locacao=loc, vigente_desde=inicio, valor=Decimal(valor), motivo="INICIAL")
        return loc

    def competencias(self, loc):
        return list(loc.cobrancas.values_list("competencia", flat=True))

    def test_vencimento_limitado_ao_fim_do_mes(self):
        loc = self.nova_locacao()
        loc.dia_vencimento = 31
        self.assertEqual(vencimento_de(loc, date(2026, 2, 1)), date(2026, 2, 28))
        self.assertEqual(vencimento_de(loc, date(2026, 3, 1)), date(2026, 3, 31))

    def test_nao_gera_antes_do_inicio_historico(self):
        loc = self.nova_locacao(inicio=date(2023, 3, 1))
        self.assertEqual(gerar_cobrancas(date(2026, 4, 15)), 3)
        self.assertEqual(self.competencias(loc), [date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)])

    def limite(self, d):
        c = Config.get()
        c.gerar_a_partir_de = d
        c.save()

    def importada(self, loc, comp, valor="100"):
        return Cobranca.objects.create(locacao=loc, competencia=comp, vencimento=vencimento_de(loc, comp),
                                       valor_esperado=Decimal(valor), quitada=True)

    def test_nao_gera_antes_de_gerar_a_partir_de(self):
        self.limite(date(2026, 11, 1))
        loc = self.nova_locacao(inicio=date(2023, 3, 1), importada=True)
        self.importada(loc, date(2026, 2, 1))
        self.assertEqual(gerar_cobrancas(date(2026, 12, 15)), 2)
        self.assertEqual(self.competencias(loc), [date(2026, 2, 1), date(2026, 11, 1), date(2026, 12, 1)])

    def test_limite_vale_para_importada_mesmo_sem_cobrancas(self):
        self.limite(date(2026, 11, 1))
        loc = self.nova_locacao(inicio=date(2025, 6, 1), importada=True)
        gerar_cobrancas(date(2026, 11, 15))
        self.assertEqual(self.competencias(loc), [date(2026, 11, 1)])

    def test_limite_nao_vale_para_locacao_manual_com_cobrancas(self):
        self.limite(date(2026, 11, 1))
        loc = self.nova_locacao(inicio=date(2026, 9, 1))
        self.importada(loc, date(2026, 9, 1))
        gerar_cobrancas(date(2026, 11, 15))
        self.assertEqual(self.competencias(loc), [date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)])

    def test_limite_nao_vale_para_locacao_nova_sem_cobrancas(self):
        self.limite(date(2026, 11, 1))
        loc = self.nova_locacao(inicio=date(2025, 6, 1))  # contrato cadastrado à mão, retroativo
        gerar_cobrancas(date(2026, 4, 15))
        self.assertEqual(self.competencias(loc), [date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)])

    def test_antecipacao_com_limite_e_cobrancas_importadas(self):
        self.limite(date(2026, 10, 1))
        loc = self.nova_locacao(inicio=date(2023, 3, 1), importada=True)
        for mes in range(2, 10):
            self.importada(loc, date(2026, mes, 1))
        gerar_cobrancas(date(2026, 10, 5))
        pag = registrar_pagamento(loc, date(2026, 10, 5), Decimal("300"))
        novas = list(loc.cobrancas.filter(competencia__gte=date(2026, 10, 1)))
        self.assertEqual([c.competencia for c in novas], [date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)])
        self.assertTrue(all(c.quitada for c in novas))
        self.assertEqual(loc.cobrancas.count(), 11)
        self.assertEqual(pag.nao_alocado, 0)

    def test_limite_com_isencao(self):
        self.limite(date(2026, 11, 1))
        loc = self.nova_locacao(inicio=date(2026, 10, 1), isencao_meses=3, importada=True)
        self.importada(loc, date(2026, 10, 1), valor="0")
        gerar_cobrancas(date(2027, 1, 15))
        self.assertEqual(list(loc.cobrancas.values_list("competencia", "valor_esperado")), [
            (date(2026, 10, 1), Decimal("0")), (date(2026, 11, 1), Decimal("0")), (date(2026, 12, 1), Decimal("0")),
            (date(2027, 1, 1), Decimal("100")),
        ])

    def test_isencao_tres_meses(self):
        loc = self.nova_locacao(inicio=date(2026, 8, 20), isencao_meses=3)
        gerar_cobrancas(date(2026, 12, 1))
        valores = list(loc.cobrancas.values_list("competencia", "valor_esperado"))
        self.assertEqual(valores, [
            (date(2026, 8, 1), Decimal("0")), (date(2026, 9, 1), Decimal("0")), (date(2026, 10, 1), Decimal("0")),
            (date(2026, 11, 1), Decimal("100")), (date(2026, 12, 1), Decimal("100")),
        ])
        self.assertEqual(situacao(loc.cobrancas.first(), date(2026, 12, 1)), "ISENTA")

    def test_idempotente(self):
        self.nova_locacao()
        self.assertEqual(gerar_cobrancas(date(2026, 4, 1)), 3)
        self.assertEqual(gerar_cobrancas(date(2026, 4, 1)), 0)

    def test_continua_apos_termino(self):
        loc = self.nova_locacao(termino=date(2026, 5, 1))
        gerar_cobrancas(date(2026, 6, 15))
        self.assertIn(date(2026, 6, 1), self.competencias(loc))

    def test_aguardando_sem_acesso(self):
        loc = self.nova_locacao()
        gerar_cobrancas(date(2026, 2, 1))
        cob = loc.cobrancas.get()
        self.assertEqual(situacao(cob, date(2026, 2, 10)), "ABERTO")
        self.assertEqual(situacao(cob, date(2026, 2, 11)), "AGUARDANDO")

    def test_atrasada_com_acesso(self):
        self.empresa.acesso_direto = True
        self.empresa.save()
        loc = self.nova_locacao()
        gerar_cobrancas(date(2026, 2, 1))
        self.assertEqual(situacao(loc.cobrancas.get(), date(2026, 2, 11)), "ATRASADA")

    def test_sem_empresa_atrasada(self):
        self.imovel.empresa = None
        self.imovel.save()
        loc = self.nova_locacao()
        gerar_cobrancas(date(2026, 2, 1))
        self.assertEqual(situacao(loc.cobrancas.get(), date(2026, 2, 11)), "ATRASADA")

    def test_parcial_e_quitar(self):
        loc = self.nova_locacao()
        gerar_cobrancas(date(2026, 2, 1))
        cob = loc.cobrancas.get()
        registrar_pagamento(loc, date(2026, 2, 10), Decimal("90"))
        self.assertEqual(situacao(cob, date(2026, 3, 1)), "PARCIAL")
        self.assertEqual(saldo(cob), Decimal("10"))
        cob.quitada = True
        cob.save()
        self.assertEqual(situacao(cob, date(2026, 3, 1)), "PAGA")
        self.assertEqual(saldo(cob), 0)

    def test_varios_pagamentos_um_mes(self):
        loc = self.nova_locacao(valor="150")
        gerar_cobrancas(date(2026, 2, 1))
        for dia in (5, 15, 25):
            registrar_pagamento(loc, date(2026, 2, dia), Decimal("50"))
        cob = loc.cobrancas.get()
        self.assertEqual(situacao(cob, date(2026, 3, 1)), "PAGA")
        self.assertTrue(cob.quitada)

    def test_antecipacao_seis_meses(self):
        loc = self.nova_locacao(inicio=date(2026, 9, 1))
        gerar_cobrancas(date(2026, 9, 1))
        pag = registrar_pagamento(loc, date(2026, 9, 1), Decimal("600"))
        cobs = list(loc.cobrancas.all())
        self.assertEqual([c.competencia for c in cobs], [
            date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1),
        ])
        self.assertTrue(all(c.quitada for c in cobs))
        self.assertEqual(pag.nao_alocado, 0)

    def test_antecipacao_para_no_termino(self):
        loc = self.nova_locacao(inicio=date(2026, 9, 1), termino=date(2026, 11, 30))
        gerar_cobrancas(date(2026, 9, 1))
        pag = registrar_pagamento(loc, date(2026, 9, 1), Decimal("500"))
        self.assertEqual(loc.cobrancas.count(), 3)
        self.assertEqual(pag.nao_alocado, Decimal("200"))

    def test_fifo_pula_isenta(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1), isencao_meses=1)
        gerar_cobrancas(date(2026, 3, 1))
        registrar_pagamento(loc, date(2026, 3, 5), Decimal("100"))
        isenta, marco = loc.cobrancas.all()
        self.assertFalse(isenta.quitada)
        self.assertEqual(isenta.recebido, 0)
        self.assertTrue(marco.quitada)

    def test_troca_de_inquilino_nao_herda(self):
        a = self.nova_locacao(inicio=date(2026, 3, 1))
        gerar_cobrancas(date(2026, 3, 1))
        encerrar_locacao(a, date(2026, 3, 31))
        a.refresh_from_db()
        self.assertFalse(a.ativa)
        self.assertEqual(a.data_saida, date(2026, 3, 31))
        b = self.nova_locacao(inicio=date(2026, 5, 1))
        self.assertEqual(b.cobrancas.count(), 0)
        gerar_cobrancas(date(2026, 7, 1))
        self.assertEqual(self.competencias(a), [date(2026, 3, 1)])
        self.assertEqual(situacao(a.cobrancas.get(), date(2026, 7, 1)), "AGUARDANDO")
        self.assertEqual(self.competencias(b), [date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1)])
        self.assertEqual(gerar_cobrancas(date(2026, 7, 1), locacao=a), 0)

    def test_alocacao_explicita_maior_que_valor(self):
        loc = self.nova_locacao()
        gerar_cobrancas(date(2026, 2, 1))
        cob = loc.cobrancas.get()
        with self.assertRaises(ValueError):
            registrar_pagamento(loc, date(2026, 2, 5), Decimal("50"), alocacoes=[(cob, Decimal("60"))])
        self.assertEqual(loc.pagamentos.count(), 0)

    def test_alocacao_explicita(self):
        loc = self.nova_locacao()
        gerar_cobrancas(date(2026, 3, 1))
        fev, mar = loc.cobrancas.all()
        pag = registrar_pagamento(loc, date(2026, 3, 5), Decimal("120"), alocacoes=[(mar, Decimal("100"))])
        mar.refresh_from_db()
        self.assertTrue(mar.quitada)
        self.assertEqual(fev.recebido, 0)
        self.assertEqual(pag.nao_alocado, Decimal("20"))
        self.assertEqual(Cobranca.objects.count(), 2)

    def test_fifo_desde_um_mes_ignora_os_anteriores(self):
        loc = self.nova_locacao()
        gerar_cobrancas(date(2026, 3, 1))
        fev, mar = loc.cobrancas.all()
        pag = registrar_pagamento(loc, date(2026, 3, 5), Decimal("250"), desde=mar.competencia)
        self.assertEqual(fev.recebido, 0)
        self.assertEqual(self.competencias(loc), [date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1),
                                                  date(2026, 5, 1)])
        self.assertEqual([c.recebido for c in loc.cobrancas.order_by("competencia")[1:]],
                         [Decimal("100"), Decimal("100"), Decimal("50")])
        self.assertEqual(pag.nao_alocado, 0)

    def test_locacao_encerrada_nao_gera_futuras(self):
        loc = self.nova_locacao(inicio=date(2026, 3, 1))
        gerar_cobrancas(date(2026, 3, 1))
        encerrar_locacao(loc, date(2026, 3, 31))
        loc.refresh_from_db()
        pag = registrar_pagamento(loc, date(2026, 4, 5), Decimal("200"))
        self.assertEqual(self.competencias(loc), [date(2026, 3, 1)])
        self.assertTrue(loc.cobrancas.get().quitada)
        self.assertEqual(pag.nao_alocado, Decimal("100"))

    def test_antecipacao_em_prorrogacao_tacita(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1), termino=date(2026, 3, 31))
        gerar_cobrancas(date(2026, 9, 1))
        registrar_pagamento(loc, date(2026, 2, 5), Decimal("800"))  # quita fev..set
        pag = registrar_pagamento(loc, date(2026, 9, 5), Decimal("200"))
        self.assertEqual(self.competencias(loc)[-2:], [date(2026, 10, 1), date(2026, 11, 1)])
        self.assertEqual(pag.nao_alocado, 0)

    def test_fifo_quita_saldo_zerado_por_ajuste(self):
        loc = self.nova_locacao()
        gerar_cobrancas(date(2026, 3, 1))
        fev, mar = loc.cobrancas.all()
        fev.ajuste = Decimal("-100")
        fev.save()
        registrar_pagamento(loc, date(2026, 3, 5), Decimal("100"))
        fev.refresh_from_db()
        mar.refresh_from_db()
        self.assertTrue(fev.quitada)
        self.assertEqual(fev.recebido, 0)
        self.assertTrue(mar.quitada)

    def test_alocacao_explicita_invalida(self):
        loc = self.nova_locacao()
        outra = self.nova_locacao()
        gerar_cobrancas(date(2026, 2, 1))
        cob_outra = outra.cobrancas.get()
        for alocs in ([(cob_outra, Decimal("10"))], [(loc.cobrancas.get(), Decimal("0"))]):
            with self.subTest(alocs), self.assertRaises(ValueError):
                registrar_pagamento(loc, date(2026, 2, 5), Decimal("50"), alocacoes=alocs)
        self.assertEqual(loc.pagamentos.count(), 0)

    def test_alocacao_explicita_acima_do_saldo(self):
        loc = self.nova_locacao()
        gerar_cobrancas(date(2026, 2, 1))
        cob = loc.cobrancas.get()
        registrar_pagamento(loc, date(2026, 2, 5), Decimal("120"), alocacoes=[(cob, Decimal("120"))])
        self.assertEqual(cob.recebido, Decimal("120"))
        self.assertEqual(situacao(cob, date(2026, 3, 1)), "PAGA")

    def test_encerrar_retroativo_apaga_meses_depois_da_saida(self):
        loc = self.nova_locacao(inicio=date(2026, 6, 1))
        gerar_cobrancas(date(2026, 11, 2))
        encerrar_locacao(loc, date(2026, 8, 31))
        self.assertEqual(self.competencias(loc), [date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1)])

    def test_encerrar_retroativo_recusa_se_mes_posterior_tem_pagamento(self):
        loc = self.nova_locacao(inicio=date(2026, 6, 1))
        gerar_cobrancas(date(2026, 11, 2))
        registrar_pagamento(loc, date(2026, 6, 5), Decimal("400"))  # jun..set
        with self.assertRaisesMessage(ValueError, "set/2026"):
            encerrar_locacao(loc, date(2026, 8, 31))
        loc.refresh_from_db()
        self.assertTrue(loc.ativa)
        self.assertEqual(len(self.competencias(loc)), 6)

    def test_desconto_pontualidade_no_prazo_quita(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1), valor="1000", desconto_pontualidade=Decimal("100"),
                                dia_limite_desconto=5)
        gerar_cobrancas(date(2026, 3, 1))
        registrar_pagamento(loc, date(2026, 2, 5), Decimal("900"))
        fev, mar = loc.cobrancas.all()
        self.assertTrue(fev.quitada)
        self.assertEqual((fev.ajuste, fev.motivo_ajuste), (Decimal("-100"), "desconto por pontualidade"))
        self.assertEqual(fev.valor_esperado, Decimal("1000"))  # esperado continua cheio
        self.assertEqual((mar.quitada, mar.ajuste, mar.recebido), (False, Decimal("0"), Decimal("0")))

    def test_desconto_pontualidade_manual_e_atrasado(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1), valor="1000", desconto_pontualidade=Decimal("100"),
                                dia_limite_desconto=31)
        gerar_cobrancas(date(2026, 3, 1))
        fev, mar = loc.cobrancas.all()
        registrar_pagamento(loc, date(2026, 2, 28), Decimal("900"), alocacoes=[(fev, Decimal("900"))])  # limite 28/02
        registrar_pagamento(loc, date(2026, 4, 1), Decimal("900"), alocacoes=[(mar, Decimal("900"))])  # atrasado
        fev.refresh_from_db()
        mar.refresh_from_db()
        self.assertTrue(fev.quitada)
        self.assertEqual(fev.ajuste, Decimal("-100"))
        self.assertEqual((mar.quitada, mar.ajuste), (False, Decimal("0")))
        self.assertEqual(situacao(mar, date(2026, 4, 2)), "PARCIAL")

    def test_excluir_pagamento_desfaz_quitacao_e_meses_antecipados(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1))
        gerar_cobrancas(date(2026, 2, 1))
        registrar_pagamento(loc, date(2026, 2, 5), Decimal("50"))
        pag = registrar_pagamento(loc, date(2026, 2, 6), Decimal("350"))  # fev..mai
        excluir_pagamento(pag, date(2026, 2, 20))
        self.assertEqual(self.competencias(loc), [date(2026, 2, 1)])
        fev = loc.cobrancas.get()
        self.assertFalse(fev.quitada)
        self.assertEqual(fev.recebido, Decimal("50"))

    def test_excluir_pagamento_mantem_quitada_por_ajuste_e_mes_atual(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1))
        gerar_cobrancas(date(2026, 3, 1))
        fev, mar = loc.cobrancas.all()
        pag = registrar_pagamento(loc, date(2026, 3, 5), Decimal("200"))
        Cobranca.objects.filter(pk=fev.pk).update(ajuste=Decimal("-100"))
        excluir_pagamento(pag, date(2026, 3, 20))
        fev.refresh_from_db()
        mar.refresh_from_db()
        self.assertTrue(fev.quitada)  # ajuste zera o saldo
        self.assertFalse(mar.quitada)  # mês atual não é apagado
        self.assertEqual(self.competencias(loc), [date(2026, 2, 1), date(2026, 3, 1)])

    def test_excluir_pagamento_desfaz_desconto_pontualidade(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1), valor="1000", desconto_pontualidade=Decimal("100"),
                                dia_limite_desconto=5)
        gerar_cobrancas(date(2026, 2, 1))
        pag = registrar_pagamento(loc, date(2026, 2, 5), Decimal("900"))
        excluir_pagamento(pag, date(2026, 2, 20))
        fev = loc.cobrancas.get()
        self.assertEqual((fev.quitada, fev.ajuste, fev.motivo_ajuste), (False, Decimal("0"), ""))

    def test_excluir_valor_futuro_volta_ao_anterior(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1))
        futuro = ValorAluguel.objects.create(locacao=loc, vigente_desde=date(2026, 3, 5), valor=Decimal("150"),
                                             motivo="ESCALONAMENTO")
        gerar_cobrancas(date(2026, 3, 1))
        mar = loc.cobrancas.get(competencia=date(2026, 3, 1))
        self.assertEqual(mar.valor_esperado, Decimal("150"))
        excluir_valor(futuro, date(2026, 3, 2))
        mar.refresh_from_db()
        self.assertEqual(mar.valor_esperado, Decimal("100"))
        self.assertFalse(loc.valores.filter(pk=futuro.pk).exists())

    def test_excluir_valor_vigente_recusa(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1))
        v = ValorAluguel.objects.create(locacao=loc, vigente_desde=date(2026, 3, 1), valor=Decimal("150"),
                                        motivo="ACORDO")
        with self.assertRaises(ValueError):
            excluir_valor(v, date(2026, 3, 1))
        with self.assertRaises(ValueError):
            excluir_valor(loc.valores.get(motivo="INICIAL"), date(2025, 1, 1))

    def test_criar_cobranca_ja_criada_por_outro_processo(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1))
        primeira = _criar_cobranca(loc, date(2026, 2, 1))
        self.assertEqual(_criar_cobranca(loc, date(2026, 2, 1)), primeira)  # corrida: não estoura IntegrityError
        self.assertEqual(loc.cobrancas.count(), 1)

    def test_desconto_pontualidade_em_dois_meses_pagos_juntos(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1), valor="1000", desconto_pontualidade=Decimal("100"),
                                dia_limite_desconto=5)
        gerar_cobrancas(date(2026, 3, 1))
        registrar_pagamento(loc, date(2026, 2, 5), Decimal("1800"))
        fev, mar = loc.cobrancas.all()
        self.assertEqual([(c.quitada, c.ajuste, c.recebido) for c in (fev, mar)],
                         [(True, Decimal("-100"), Decimal("900"))] * 2)

    def test_excluir_pagamento_nao_apaga_mes_futuro_quitado_a_mao(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1))
        gerar_cobrancas(date(2026, 2, 1))
        pag = registrar_pagamento(loc, date(2026, 2, 6), Decimal("200"))  # fev e mar
        Cobranca.objects.filter(competencia=date(2026, 4, 1)).delete()
        abr = Cobranca.objects.create(locacao=loc, competencia=date(2026, 4, 1), vencimento=date(2026, 4, 10),
                                      valor_esperado=Decimal("100"), quitada=True)
        excluir_pagamento(pag, date(2026, 2, 20))
        self.assertTrue(Cobranca.objects.filter(pk=abr.pk).exists())

    def _com_desconto(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1), valor="1000", desconto_pontualidade=Decimal("100"),
                                dia_limite_desconto=5)
        gerar_cobrancas(date(2026, 3, 1))
        return loc

    def test_desconto_pontualidade_pago_cheio_no_prazo_nao_desconta(self):
        loc = self._com_desconto()
        registrar_pagamento(loc, date(2026, 2, 5), Decimal("1000"))
        fev, mar = loc.cobrancas.all()
        self.assertEqual((fev.quitada, fev.recebido, fev.ajuste), (True, Decimal("1000"), Decimal("0")))
        self.assertEqual((mar.quitada, mar.recebido), (False, Decimal("0")))

    def test_desconto_pontualidade_dois_meses_atrasado_paga_cheio(self):
        loc = self._com_desconto()
        registrar_pagamento(loc, date(2026, 3, 20), Decimal("1800"))
        fev, mar = loc.cobrancas.all()
        self.assertEqual((fev.quitada, fev.recebido, fev.ajuste), (True, Decimal("1000"), Decimal("0")))
        self.assertEqual((mar.quitada, mar.recebido, mar.ajuste), (False, Decimal("800"), Decimal("0")))
        self.assertEqual(situacao(mar, date(2026, 3, 21)), "PARCIAL")
