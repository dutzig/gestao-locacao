from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from locacao.cobrancas import gerar_cobrancas, saldo
from locacao.indices import IndiceIndisponivel
from locacao.templatetags.br import brl
from locacao.models import (
    Cobranca, ComissaoMes, Empresa, Imovel, Inquilino, Locacao, Pagamento, Reajuste, RevisaoImportacao, TarefaManual,
    ValorAluguel,
)

MSG_INDICE = "Não foi possível obter o índice do Banco Central. Informe o percentual manualmente."


class FluxosTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin", password="senha-teste-123", is_staff=True)
        self.client.force_login(self.admin)
        self.hoje = timezone.localdate()
        self.empresa = Empresa.objects.create(nome="Empresa Teste", acesso_direto=True)
        self.imovel = Imovel.objects.create(codigo="UF-001", empresa=self.empresa)
        inquilino = Inquilino.objects.create(nome="Inquilino Teste", documento="000.000.000-00")
        self.loc = Locacao.objects.create(
            imovel=self.imovel, inquilino=inquilino, inicio=date(2026, 2, 1), dia_vencimento=10, indice="IGPM",
            mes_base_reajuste=3, link_contrato="https://exemplo.test/contrato",
        )
        ValorAluguel.objects.create(locacao=self.loc, vigente_desde=date(2026, 2, 1), valor=Decimal("100"),
                                    motivo="INICIAL")
        gerar_cobrancas(self.hoje, locacao=self.loc)

    def cobranca(self, mes):
        return self.loc.cobrancas.get(competencia=date(2026, mes, 1))

    # --- pagamento ---

    def pagar(self, cobranca, follow=True, **dados):
        dados.setdefault("saldo", str(saldo(cobranca)))  # o saldo que a página mostrou
        return self.client.post(reverse("cobranca_pagar", args=[cobranca.pk]), dados, follow=follow)

    def test_pagar_rapido_formulario_preenchido_com_saldo_e_vencimento(self):
        fev = self.cobranca(2)
        resp = self.client.get(reverse("dashboard"), {"mes": "2026-02"})
        self.assertContains(resp, '<summary>Registrar pagamento <span aria-hidden="true">▾</span>')
        self.assertContains(resp, f'action="{reverse("cobranca_pagar", args=[fev.pk])}"')
        self.assertContains(resp, f'id="pg{fev.pk}-valor" value="100,00"')
        self.assertContains(resp, f'id="pg{fev.pk}-data" value="2026-02-10"')
        self.assertContains(resp, "Data em que o dinheiro entrou")
        self.assertContains(resp, '<input type="hidden" name="voltar" value="/?mes=2026-02">', html=True)
        self.assertContains(resp, '<input type="hidden" name="saldo" value="100.00">', html=True)
        loc = self.client.get(reverse("locacao", args=[self.loc.pk]))
        self.assertContains(loc, f'action="{reverse("cobranca_pagar", args=[fev.pk])}"')

    def test_pagar_rapido_saldo_exato_quita_e_vai_para_recebidos(self):
        fev = self.cobranca(2)
        resp = self.pagar(fev, valor="100,00", data="2026-02-14", voltar="/?mes=2026-02")
        self.assertRedirects(resp, "/?mes=2026-02")
        self.assertContains(resp, "Pagamento registrado. 1 mês quitado.")
        fev.refresh_from_db()
        self.assertTrue(fev.quitada)
        self.assertEqual(Pagamento.objects.get().data, date(2026, 2, 14))
        r = resp.context["r"]
        self.assertEqual([l.cobranca for l in r.recebidos], [fev])
        self.assertNotIn(fev, [l.cobranca for l in r.pendentes])

    def test_pagar_rapido_parcial(self):
        mar = self.cobranca(3)
        self.pagar(mar, valor="40", data="2026-03-10")
        self.assertEqual(self.client.get(reverse("dashboard"), {"mes": "2026-03"}).context["r"].pendentes[0].situacao,
                         "PARCIAL")
        self.assertEqual(self.cobranca(2).recebido, 0)

    def test_pagar_rapido_acima_do_saldo_segue_para_o_mes_seguinte(self):
        fev = self.cobranca(2)
        resp = self.pagar(fev, valor="150", data="2026-02-10")
        self.assertRedirects(resp, "/?mes=2026-02")  # sem voltar: dashboard do mês da cobrança
        self.assertContains(resp, "1 mês quitado")
        self.assertTrue(self.cobranca(2).quitada)
        self.assertEqual(self.cobranca(3).recebido, Decimal("50"))

    def test_pagar_rapido_entrada_invalida(self):
        fev = self.cobranca(2)
        for dados, campo in (({"valor": "abc", "data": "2026-02-10"}, "Valor recebido"),
                             ({"valor": "0", "data": "2026-02-10"}, "Valor recebido"),
                             ({"valor": "100", "data": ""}, "Data do pagamento")):
            with self.subTest(dados=dados):
                resp = self.pagar(fev, voltar=f"/locacoes/{self.loc.pk}/", **dados)
                self.assertRedirects(resp, reverse("locacao", args=[self.loc.pk]))
                self.assertContains(resp, f"Pagamento não registrado. {campo}:")
        self.assertFalse(Pagamento.objects.exists())

    def test_pagar_rapido_cobranca_ja_quitada(self):
        fev = self.cobranca(2)
        self.pagar(fev, valor="100", data="2026-02-10")
        self.assertContains(self.pagar(fev, valor="100", data="2026-02-10"), "já está quitada")
        self.assertEqual(Pagamento.objects.count(), 1)

    def test_pagar_rapido_valor_com_milhar(self):
        ValorAluguel.objects.filter(locacao=self.loc).update(valor=Decimal("21600"))
        Cobranca.objects.filter(locacao=self.loc).update(valor_esperado=Decimal("21600"))
        fev = self.cobranca(2)
        self.assertContains(self.client.get(reverse("dashboard"), {"mes": "2026-02"}),
                            f'id="pg{fev.pk}-valor" value="21.600,00"')
        self.pagar(fev, valor="21.600,00", data="2026-02-10")
        self.assertTrue(self.cobranca(2).quitada)

    def test_pagar_rapido_envio_em_dobro_registra_um_so(self):
        fev = self.cobranca(2)
        self.pagar(fev, valor="40", data="2026-02-10")
        for _ in range(2):
            resp = self.pagar(fev, valor="60", data="2026-02-12", saldo="60.00")
        self.assertContains(resp, "A cobrança mudou desde que a página foi aberta; confira e tente de novo.")
        self.assertEqual(Pagamento.objects.count(), 2)
        self.assertEqual(self.cobranca(3).recebido, 0)

    def test_pagar_rapido_acima_do_saldo_mantem_desconto_de_pontualidade(self):
        Locacao.objects.filter(pk=self.loc.pk).update(desconto_pontualidade=Decimal("10"), dia_limite_desconto=10)
        self.loc.refresh_from_db()
        mar = self.cobranca(3)
        resp = self.pagar(mar, valor="180", data="2026-03-05")
        self.assertContains(resp, "2 meses quitados")
        self.assertTrue(self.cobranca(3).quitada)
        self.assertTrue(self.cobranca(4).quitada)
        self.assertEqual((saldo(self.cobranca(3)), saldo(self.cobranca(4))), (0, 0))
        self.assertEqual(Pagamento.objects.get().nao_alocado, 0)
        self.assertEqual(self.cobranca(2).recebido, 0)  # mês anterior em aberto fica como estava

    def test_pagar_rapido_cobranca_isenta(self):
        fev = self.cobranca(2)
        Cobranca.objects.filter(pk=fev.pk).update(valor_esperado=0)
        self.assertContains(self.pagar(fev, valor="10", data="2026-02-10", saldo="0"),
                            "Esta cobrança não tem valor a receber.")
        self.assertFalse(Pagamento.objects.exists())

    def test_pagar_rapido_ignora_voltar_externo(self):
        resp = self.pagar(self.cobranca(2), follow=False, valor="100", data="2026-02-10",
                          voltar="https://externo.test/")
        self.assertRedirects(resp, "/?mes=2026-02", fetch_redirect_response=False)

    def test_pagar_rapido_get_nao_permitido(self):
        self.assertEqual(self.client.get(reverse("cobranca_pagar", args=[self.cobranca(2).pk])).status_code, 405)

    def test_pagamento_automatico_quita_tres_meses(self):
        url = reverse("pagamento_novo", args=[self.loc.pk])
        form = self.client.get(url)
        self.assertContains(form, "<details")
        self.assertContains(form, "Distribuir manualmente entre os meses")
        self.assertContains(form, f'value="{self.hoje:%Y-%m-%d}"')
        resp = self.client.post(url, {"data": "2026-04-15", "valor": "300,00"}, follow=True)
        self.assertRedirects(resp, reverse("locacao", args=[self.loc.pk]))
        self.assertContains(resp, "3 meses quitados")
        quitadas = list(self.loc.cobrancas.filter(quitada=True).values_list("competencia", flat=True))
        self.assertEqual(quitadas, [date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)])

    def test_pagamento_com_alocacao_manual(self):
        jun = self.cobranca(6)
        resp = self.client.post(reverse("pagamento_novo", args=[self.loc.pk]), {
            "data": "2026-07-01", "valor": "150", f"aloc_{jun.pk}": "100", f"aloc_{self.cobranca(7).pk}": "",
        }, follow=True)
        self.assertContains(resp, "1 mês quitado")
        self.assertContains(resp, "R$ 50,00 não foi distribuído")
        self.assertEqual(list(self.loc.cobrancas.filter(quitada=True)), [jun])
        self.assertEqual(Pagamento.objects.get().alocacoes.get().cobranca, jun)

    def test_pagamento_alocacao_maior_que_valor_e_erro_do_form(self):
        resp = self.client.post(reverse("pagamento_novo", args=[self.loc.pk]), {
            "data": "2026-07-01", "valor": "50", f"aloc_{self.cobranca(6).pk}": "100",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alocações somam mais que o valor do pagamento.")
        self.assertContains(resp, "<details open")
        self.assertFalse(Pagamento.objects.exists())

    def test_alocacao_manual_nao_passa_do_saldo_do_mes(self):
        resp = self.client.post(reverse("pagamento_novo", args=[self.loc.pk]), {
            "data": "2026-07-01", "valor": "300", f"aloc_{self.cobranca(6).pk}": "300",
        })
        self.assertContains(resp, "O valor para jun/2026 não pode passar do saldo de R$ 100,00.")
        self.assertFalse(Pagamento.objects.exists())

    def test_pagamento_desabilita_botao_ao_enviar(self):
        self.assertContains(self.client.get(reverse("pagamento_novo", args=[self.loc.pk])), "submitter.disabled = true")

    def test_pagamento_exige_data(self):
        resp = self.client.post(reverse("pagamento_novo", args=[self.loc.pk]), {"valor": "100"})
        self.assertContains(resp, 'href="#id_data"')
        self.assertFalse(Pagamento.objects.exists())

    def test_excluir_pagamento_confirma_e_reabre_meses(self):
        self.client.post(reverse("pagamento_novo", args=[self.loc.pk]), {"data": "2026-04-15", "valor": "200"})
        p = self.loc.pagamentos.get()
        detalhe = self.client.get(reverse("locacao", args=[self.loc.pk]))
        url = reverse("pagamento_excluir", args=[p.pk])
        self.assertContains(detalhe, f'href="{url}"')
        confirma = self.client.get(url)
        self.assertContains(confirma, "fev/2026, mar/2026")
        self.assertTrue(Pagamento.objects.filter(pk=p.pk).exists())  # GET não exclui
        resp = self.client.post(url, follow=True)
        self.assertRedirects(resp, reverse("locacao", args=[self.loc.pk]))
        self.assertContains(resp, "Pagamento excluído.")
        self.assertFalse(Pagamento.objects.exists())
        self.assertFalse(self.loc.cobrancas.filter(quitada=True).exists())

    def test_excluir_pagamento_de_mes_com_comissao_recebida_avisa(self):
        self.client.post(reverse("pagamento_novo", args=[self.loc.pk]), {"data": "2026-02-08", "valor": "100"})
        p = self.loc.pagamentos.get()
        url = reverse("pagamento_excluir", args=[p.pk])
        self.assertNotContains(self.client.get(url), "comissão")
        ComissaoMes.objects.create(mes=date(2026, 2, 1), recebida_em=date(2026, 3, 5))
        self.assertContains(self.client.get(url), "Comissão de fev/2026 já marcada como recebida")
        resp = self.client.post(url, follow=True)
        self.assertContains(resp, "Comissão de fev/2026 já marcada como recebida")
        self.assertFalse(Pagamento.objects.exists())

    def test_excluir_valor_futuro(self):
        futuro = ValorAluguel.objects.create(locacao=self.loc, vigente_desde=date(self.hoje.year + 1, 1, 1),
                                             valor=Decimal("150"), motivo="ESCALONAMENTO")
        url = reverse("valor_excluir", args=[futuro.pk])
        self.assertContains(self.client.get(reverse("locacao", args=[self.loc.pk])), f'href="{url}"')
        self.assertContains(self.client.get(url), "Excluir valor")
        self.client.post(url)
        self.assertFalse(ValorAluguel.objects.filter(pk=futuro.pk).exists())
        inicial = self.loc.valores.get()
        resp = self.client.post(reverse("valor_excluir", args=[inicial.pk]), follow=True)
        self.assertContains(resp, "Só se exclui valor futuro")
        self.assertTrue(self.loc.valores.exists())

    def test_dividas_agrupa_por_locacao_com_situacao_e_total(self):
        from locacao.cobrancas import encerrar_locacao
        encerrar_locacao(self.loc, self.hoje)
        self.loc.situacao_divida = "AMIGAVEL"
        self.loc.save()
        ativa = Locacao.objects.create(
            imovel=Imovel.objects.create(codigo="UF-002", empresa=self.empresa),
            inquilino=Inquilino.objects.create(nome="Inquilino Dois"), inicio=date(2026, 2, 1), dia_vencimento=10,
            indice="IGPM", situacao_divida="JURIDICO",
        )
        ValorAluguel.objects.create(locacao=ativa, vigente_desde=date(2026, 2, 1), valor=Decimal("50"), motivo="INICIAL")
        sem_divida = Locacao.objects.create(
            imovel=Imovel.objects.create(codigo="UF-003", empresa=self.empresa), inquilino=ativa.inquilino,
            inicio=date(2026, 2, 1), dia_vencimento=10, indice="IGPM",
        )
        ValorAluguel.objects.create(locacao=sem_divida, vigente_desde=date(2026, 2, 1), valor=Decimal("70"),
                                    motivo="INICIAL")
        resp = self.client.get(reverse("dividas"))
        self.assertContains(resp, "Amigável")
        self.assertContains(resp, "Jurídico")
        self.assertContains(resp, "UF-002")
        self.assertNotContains(resp, "UF-003")
        vencidas = ativa.cobrancas.filter(vencimento__lt=self.hoje).count()  # da ativa, só as vencidas
        total = self.loc.cobrancas.count() * 100 + vencidas * 50
        self.assertContains(resp, f"Total: <strong>{brl(Decimal(total))}</strong>")
        self.assertContains(self.client.get(reverse("tarefas")), f'href="{reverse("dividas")}"')

    def test_edicoes_marcam_locacao_editada(self):
        futuro = ValorAluguel.objects.create(locacao=self.loc, vigente_desde=date(self.hoje.year + 1, 1, 1),
                                             valor=Decimal("150"), motivo="ESCALONAMENTO")
        dados_loc = {"imovel": self.imovel.pk, "inquilino": self.loc.inquilino_id, "inicio": "2026-02-01",
                     "dia_vencimento": "10", "indice": "IGPM", "isencao_meses": "0"}
        acoes = {
            "editar": lambda: self.client.post(reverse("locacao_editar", args=[self.loc.pk]), dados_loc),
            "situação": lambda: self.client.post(reverse("situacao_divida", args=[self.loc.pk]),
                                                 {"situacao_divida": "NENHUMA"}),
            "novo valor": lambda: self.client.post(reverse("valor_novo", args=[self.loc.pk]), {
                "vigente_desde": f"{self.hoje.year + 2}-01-01", "valor": "200", "motivo": "ACORDO"}),
            "excluir valor": lambda: self.client.post(reverse("valor_excluir", args=[futuro.pk])),
            "cobrança": lambda: self.client.post(reverse("cobranca_editar", args=[self.cobranca(2).pk]),
                                                 {"ajuste": "0", "observacoes": "nota"}),
            "encerrar": lambda: self.client.post(reverse("locacao_encerrar", args=[self.loc.pk]),
                                                 {"data_saida": f"{self.hoje:%Y-%m-%d}"}),
        }
        for nome, acao in acoes.items():
            Locacao.objects.filter(pk=self.loc.pk).update(editada=False)
            with self.subTest(nome):
                self.assertEqual(acao().status_code, 302)
                self.loc.refresh_from_db()
                self.assertTrue(self.loc.editada)

    def test_locacao_mostra_acoes_da_cobranca(self):
        resp = self.client.get(reverse("locacao", args=[self.loc.pk]))
        self.assertContains(resp, reverse("cobranca_editar", args=[self.cobranca(2).pk]))
        self.assertContains(resp, reverse("pagamento_novo", args=[self.loc.pk]))

    # --- cobrança ---

    def test_marcar_cobranca_quitada(self):
        c = self.cobranca(3)
        resp = self.client.post(reverse("cobranca_editar", args=[c.pk]), {
            "ajuste": "-10,00", "motivo_ajuste": "reparo", "quitada": "on", "observacoes": "",
        })
        self.assertRedirects(resp, reverse("locacao", args=[self.loc.pk]), fetch_redirect_response=False)
        c.refresh_from_db()
        self.assertTrue(c.quitada)
        self.assertEqual(c.ajuste, Decimal("-10"))

    # --- dívida ---

    def test_situacao_divida_e_anotacao(self):
        resp = self.client.post(reverse("situacao_divida", args=[self.loc.pk]), {"situacao_divida": "AMIGAVEL"})
        self.assertRedirects(resp, reverse("locacao", args=[self.loc.pk]), fetch_redirect_response=False)
        self.client.post(reverse("anotacao_nova", args=[self.loc.pk]), {"data": "2026-10-01", "texto": "Ligação feita"})
        self.loc.refresh_from_db()
        self.assertEqual(self.loc.situacao_divida, "AMIGAVEL")
        pagina = self.client.get(reverse("locacao", args=[self.loc.pk]))
        self.assertContains(pagina, "Ligação feita")
        self.assertContains(pagina, '<option value="AMIGAVEL" selected>')
        self.assertEqual(self.client.get(reverse("situacao_divida", args=[self.loc.pk])).status_code, 405)

    # --- reajuste ---

    def test_reajuste_do_calculo_ate_aplicado(self):
        with mock.patch("locacao.indices.buscar_variacoes", return_value=[Decimal("1")] * 12):
            self.client.post(reverse("reajuste_calcular", args=[self.loc.pk]), {"ano": "2027"})
        r = Reajuste.objects.get()
        self.assertEqual(r.valor_calculado, Decimal("112.68"))
        pagina = self.client.get(reverse("reajustes"), {"ano": "2027"})
        self.assertContains(pagina, "R$ 112,68")
        self.assertContains(pagina, "12,68 %")
        self.client.post(reverse("reajuste_enviado", args=[r.pk]))
        self.client.post(reverse("reajuste_aprovar", args=[r.pk]), {"valor_aprovado": "110,00"})
        confirmar = self.client.get(reverse("reajuste_aplicar", args=[r.pk]))
        self.assertContains(confirmar, "R$ 100,00 → R$ 110,00")
        self.assertContains(confirmar, f'action="{reverse("reajuste_aplicar", args=[r.pk])}"')
        r.refresh_from_db()
        self.assertIsNone(r.aplicado_em)  # GET só confirma
        self.assertFalse(self.loc.valores.filter(motivo="REAJUSTE").exists())
        resp = self.client.post(reverse("reajuste_aplicar", args=[r.pk]))
        self.assertRedirects(resp, reverse("reajustes") + "?ano=2027", fetch_redirect_response=False)
        r.refresh_from_db()
        self.assertEqual((r.decisao, r.valor_aprovado, r.aplicado_em), ("APROVADO", Decimal("110"), self.hoje))
        self.assertEqual(self.loc.valores.get(motivo="REAJUSTE").valor, Decimal("110"))

    def test_aplicar_com_valor_na_mesma_data_mostra_erro(self):
        ValorAluguel.objects.create(locacao=self.loc, vigente_desde=date(2027, 3, 1), valor=Decimal("105"),
                                    motivo="ACORDO")
        r = Reajuste.objects.create(locacao=self.loc, ano=2027, indice="IGPM", valor_atual=Decimal("100"),
                                    vigente_desde=date(2027, 3, 1), decisao="APROVADO", valor_aprovado=Decimal("110"))
        resp = self.client.post(reverse("reajuste_aplicar", args=[r.pk]), follow=True)
        self.assertContains(resp, "Já existe um valor do aluguel em 01/03/2027")

    def test_reajuste_enviado_nao_recalcula(self):
        url = reverse("reajuste_calcular", args=[self.loc.pk])
        with mock.patch("locacao.indices.buscar_variacoes", return_value=[Decimal("1")] * 12):
            self.client.post(url, {"ano": "2027"})
        r = Reajuste.objects.get()
        self.client.post(reverse("reajuste_enviado", args=[r.pk]))
        resp = self.client.post(url, {"ano": "2027", "percentual": "50"}, follow=True)
        self.assertContains(resp, "não pode ser recalculado")
        r.refresh_from_db()
        self.assertEqual(r.valor_calculado, Decimal("112.68"))

    def test_aprovar_exige_valor_positivo(self):
        r = Reajuste.objects.create(locacao=self.loc, ano=2027, indice="IGPM", valor_atual=Decimal("100"),
                                    valor_calculado=Decimal("105"), vigente_desde=date(2027, 3, 1),
                                    enviado_em=date(2027, 2, 1))
        self.client.post(reverse("reajuste_aprovar", args=[r.pk]), {"valor_aprovado": "0"})
        r.refresh_from_db()
        self.assertEqual(r.decisao, "PENDENTE")

    def test_reajuste_recusado(self):
        with mock.patch("locacao.indices.buscar_variacoes", return_value=[Decimal("1")] * 12):
            self.client.post(reverse("reajuste_calcular", args=[self.loc.pk]), {"ano": "2027"})
        r = Reajuste.objects.get()
        self.client.post(reverse("reajuste_recusar", args=[r.pk]))
        r.refresh_from_db()
        self.assertEqual(r.decisao, "RECUSADO")

    def test_reajuste_falha_do_bcb_mostra_campo_manual(self):
        url = reverse("reajuste_calcular", args=[self.loc.pk])
        for erro in (IndiceIndisponivel("fora do ar"), ValueError("série")):
            with self.subTest(erro=erro), mock.patch("locacao.indices.buscar_variacoes", side_effect=erro):
                resp = self.client.post(url, {"ano": "2027"}, follow=True)
                self.assertContains(resp, MSG_INDICE)
                self.assertContains(resp, 'name="percentual"')
        self.assertFalse(Reajuste.objects.exists())
        self.client.post(url, {"ano": "2027", "percentual": "5,5"})
        r = Reajuste.objects.get()
        self.assertEqual((r.percentual, r.valor_calculado), (Decimal("5.5"), Decimal("105.50")))

    def test_reajuste_sem_mes_base_nao_calcula(self):
        self.loc.mes_base_reajuste = None
        self.loc.save()
        resp = self.client.post(reverse("reajuste_calcular", args=[self.loc.pk]), {"ano": "2027"}, follow=True)
        self.assertContains(resp, "não tem índice ou mês-base")
        self.assertFalse(Reajuste.objects.exists())
        self.assertEqual(self.client.get(reverse("reajustes"), {"ano": "99999999999999999999"}).status_code, 200)

    def test_reajuste_devido_aparece_com_botao_calcular(self):
        loc = Locacao.objects.create(
            imovel=Imovel.objects.create(codigo="UF-002"), inquilino=self.loc.inquilino, inicio=date(2025, 1, 1),
            dia_vencimento=5, indice="IPCA", mes_base_reajuste=1,
        )
        resp = self.client.get(reverse("reajustes"))
        self.assertContains(resp, "UF-002")
        self.assertContains(resp, reverse("reajuste_calcular", args=[loc.pk]))

    # --- comissão ---

    def test_marcar_comissao_recebida_tira_tarefa(self):
        self.client.post(reverse("pagamento_novo", args=[self.loc.pk]), {"data": "2026-02-10", "valor": "100"})
        self.assertContains(self.client.get(reverse("tarefas")), "Comissão de fev/2026 a receber")
        pagina = self.client.get(reverse("comissoes"))
        self.assertContains(pagina, "R$ 5,00")
        self.assertContains(pagina, "Pela competência")
        self.assertContains(pagina, reverse("config"))
        resp = self.client.post(reverse("comissao_recebida", args=["2026-02"]), {"recebida_em": "2026-03-05"})
        self.assertRedirects(resp, reverse("comissoes"), fetch_redirect_response=False)
        self.assertEqual(ComissaoMes.objects.get().recebida_em, date(2026, 3, 5))
        self.assertNotContains(self.client.get(reverse("tarefas")), "Comissão de fev/2026 a receber")
        self.assertEqual(self.client.post(reverse("comissao_recebida", args=["2026-13"])).status_code, 404)
        atual = f"{self.hoje:%Y-%m}"
        for mes in ("2026-01", atual):  # antes do histórico; mês ainda aberto
            with self.subTest(mes=mes):
                resp = self.client.post(reverse("comissao_recebida", args=[mes]), {"recebida_em": "2026-03-05"})
                self.assertEqual(resp.status_code, 404)
        self.assertEqual(ComissaoMes.objects.count(), 1)

    def test_marcar_comissoes_recebidas_ate_um_mes(self):
        self.client.post(reverse("pagamento_novo", args=[self.loc.pk]), {"data": "2026-02-10", "valor": "300"})
        self.assertContains(self.client.get(reverse("comissoes")), reverse("comissoes_recebidas_ate"))
        resp = self.client.post(reverse("comissoes_recebidas_ate"), {"ate": "2026-03", "recebida_em": "2026-04-05"},
                                follow=True)
        self.assertContains(resp, "2 meses marcados como recebidos")
        self.assertEqual(list(ComissaoMes.objects.values_list("mes", "recebida_em")), [
            (date(2026, 2, 1), date(2026, 4, 5)), (date(2026, 3, 1), date(2026, 4, 5)),
        ])
        resp = self.client.post(reverse("comissoes_recebidas_ate"), {"ate": "", "recebida_em": ""}, follow=True)
        self.assertContains(resp, "Informe o mês e a data")

    # --- aguardando, revisão, tarefas ---

    def test_aguardando_agrupa_por_imovel(self):
        self.empresa.acesso_direto = False
        self.empresa.save()
        resp = self.client.get(reverse("aguardando"))
        self.assertContains(resp, "UF-001")
        self.assertContains(resp, "fev/2026")
        self.assertContains(resp, f"{(self.hoje - self.cobranca(2).vencimento).days} dias")
        self.assertContains(resp, f'action="{reverse("cobranca_pagar", args=[self.cobranca(2).pk])}"')

    def test_aguardando_marca_parcial(self):
        self.empresa.acesso_direto = False
        self.empresa.save()
        self.client.post(reverse("pagamento_novo", args=[self.loc.pk]), {"data": "2026-02-10", "valor": "40"})
        self.assertContains(self.client.get(reverse("aguardando")), "Aguardando (parcial)")

    def test_tarefas_mesma_prioridade_em_ordem_de_mes(self):
        html = self.client.get(reverse("tarefas")).content.decode()
        self.assertLess(html.index("aluguel de fev/2026"), html.index("aluguel de abr/2026"))

    def test_revisao_resolver(self):
        item = RevisaoImportacao.objects.create(aba="Aba Teste", linha=7, motivo="valor ilegível")
        self.assertContains(self.client.get(reverse("revisao")), "valor ilegível")
        url = reverse("revisao_resolver", args=[item.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertRedirects(self.client.post(url), reverse("revisao"), fetch_redirect_response=False)
        item.refresh_from_db()
        self.assertTrue(item.resolvida)
        self.assertNotContains(self.client.get(reverse("revisao")), "valor ilegível")

    def test_tarefas_lista_em_grupos_com_formulario_rapido(self):
        t = TarefaManual.objects.create(titulo="Tarefa Teste")
        resp = self.client.get(reverse("tarefas"))
        self.assertContains(resp, "Urgente")
        self.assertContains(resp, "aluguel de fev/2026 atrasado")
        self.assertContains(resp, f'href="{reverse("locacao", args=[self.loc.pk])}"')
        self.assertContains(resp, f'action="{reverse("tarefa_nova")}"')
        self.assertContains(resp, reverse("tarefa_concluir", args=[t.pk]))

    def test_nav_staff_tem_reajustes_e_comissoes(self):
        resp = self.client.get(reverse("tarefas"))
        self.assertContains(resp, f'href="{reverse("reajustes")}"')
        self.assertContains(resp, f'href="{reverse("comissoes")}"')


class LoginRedirecionaTests(TestCase):
    def test_staff_vai_para_tarefas_e_visualizacao_para_dashboard(self):
        User.objects.create_user("admin", password="senha-teste-123", is_staff=True)
        User.objects.create_user("visualizacao", password="senha-teste-123")
        for usuario, destino in (("admin", "/tarefas/"), ("visualizacao", "/")):
            with self.subTest(usuario=usuario):
                resp = self.client.post("/login/", {"username": usuario, "password": "senha-teste-123"}, follow=True)
                self.assertEqual(resp.redirect_chain[-1][0], destino)
                self.client.logout()


class VisualizacaoBloqueadaTests(TestCase):
    def test_403_em_todas_as_urls_de_fluxo(self):
        inquilino = Inquilino.objects.create(nome="Inquilino Teste")
        loc = Locacao.objects.create(imovel=Imovel.objects.create(codigo="UF-001"), inquilino=inquilino,
                                     inicio=date(2026, 2, 1), dia_vencimento=10, indice="IGPM")
        c = Cobranca.objects.create(locacao=loc, competencia=date(2026, 2, 1), vencimento=date(2026, 2, 10),
                                    valor_esperado=Decimal("100"))
        r = Reajuste.objects.create(locacao=loc, ano=2027, indice="IGPM", valor_atual=Decimal("100"),
                                    vigente_desde=date(2027, 3, 1))
        item = RevisaoImportacao.objects.create(aba="Aba Teste", linha=1, motivo="teste")
        v = ValorAluguel.objects.create(locacao=loc, vigente_desde=date(2099, 1, 1), valor=1, motivo="ACORDO")
        pag = Pagamento.objects.create(locacao=loc, data=date(2026, 2, 5), valor=Decimal("100"))
        urls = [
            ("pagamento_novo", [loc.pk]), ("cobranca_editar", [c.pk]), ("anotacao_nova", [loc.pk]),
            ("situacao_divida", [loc.pk]), ("reajustes", []), ("reajuste_calcular", [loc.pk]),
            ("reajuste_enviado", [r.pk]), ("reajuste_aprovar", [r.pk]), ("reajuste_recusar", [r.pk]),
            ("reajuste_aplicar", [r.pk]), ("comissoes", []), ("comissao_recebida", ["2026-02"]),
            ("aguardando", []), ("revisao", []), ("revisao_resolver", [item.pk]),
            ("valor_excluir", [v.pk]), ("pagamento_excluir", [pag.pk]),
            ("dividas", []), ("comissoes_recebidas_ate", []), ("cobranca_pagar", [c.pk]),
        ]
        self.client.force_login(User.objects.create_user("visualizacao", password="senha-teste-123"))
        for nome, args in urls:
            url = reverse(nome, args=args)
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url).status_code, 403)
        r.refresh_from_db()
        self.assertIsNone(r.enviado_em)
        self.assertEqual(list(Pagamento.objects.all()), [pag])
        self.assertTrue(ValorAluguel.objects.filter(pk=v.pk).exists())
