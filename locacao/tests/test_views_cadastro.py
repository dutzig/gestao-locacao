from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from locacao.cobrancas import gerar_cobrancas, registrar_pagamento
from locacao.dinheiro import primeiro_dia, somar_meses
from locacao.models import (
    ComissaoMes, Config, Despesa, Empresa, Imovel, Inquilino, Locacao, TarefaManual, ValorAluguel,
)


class CadastroTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin", password="senha-teste-123", is_staff=True)
        self.client.force_login(self.admin)
        self.empresa = Empresa.objects.create(nome="Empresa Teste", acesso_direto=True)
        self.imovel = Imovel.objects.create(codigo="UF-001", empresa=self.empresa)
        self.inquilino = Inquilino.objects.create(nome="Inquilino Teste", documento="000.000.000-00")

    def nova_locacao(self, inicio=date(2026, 2, 1)):
        loc = Locacao.objects.create(
            imovel=self.imovel, inquilino=self.inquilino, inicio=inicio, dia_vencimento=10, indice="IGPM",
        )
        ValorAluguel.objects.create(locacao=loc, vigente_desde=inicio, valor=Decimal("1000"), motivo="INICIAL")
        return loc

    def dados_locacao(self, **kw):
        d = {
            "imovel": self.imovel.pk, "inquilino": self.inquilino.pk, "inicio": "2026-03-01", "dia_vencimento": "10",
            "indice": "IGPM", "isencao_meses": "0", "valor_inicial": "1.234,56",
        }
        d.update(kw)
        return d

    def test_criar_imovel_e_ver_na_lista(self):
        resp = self.client.post(reverse("imovel_novo"), {
            "codigo": "UF-002", "cidade": "Cidade Teste", "empresa": self.empresa.pk, "participacao_pct": "80,00",
        })
        imovel = Imovel.objects.get(codigo="UF-002")
        self.assertRedirects(resp, reverse("imovel", args=[imovel.pk]), fetch_redirect_response=False)
        self.assertEqual(imovel.participacao_pct, Decimal("80"))
        lista = self.client.get(reverse("imoveis"))
        self.assertContains(lista, "UF-002")
        self.assertContains(lista, "Vago")

    def test_busca_na_lista(self):
        Imovel.objects.create(codigo="UF-099")
        resp = self.client.get(reverse("imoveis"), {"q": "099"})
        self.assertContains(resp, "UF-099")
        self.assertNotContains(resp, "UF-001")

    def test_lista_vazia_mostra_estado_vazio(self):
        resp = self.client.get(reverse("empresas"), {"q": "nada-assim"})
        self.assertContains(resp, "Nada encontrado")
        self.assertContains(self.client.get(reverse("despesas")), "Nenhuma despesa cadastrada")

    def test_criar_locacao_com_valor_inicial(self):
        resp = self.client.post(reverse("locacao_nova"), self.dados_locacao())
        loc = Locacao.objects.get()
        self.assertRedirects(resp, reverse("locacao", args=[loc.pk]), fetch_redirect_response=False)
        valor = loc.valores.get()
        self.assertEqual((valor.vigente_desde, valor.valor, valor.motivo), (date(2026, 3, 1), Decimal("1234.56"), "INICIAL"))

    def test_locacao_nova_preenche_imovel(self):
        resp = self.client.get(reverse("locacao_nova"), {"imovel": self.imovel.pk})
        self.assertContains(resp, f'<option value="{self.imovel.pk}" selected>')

    def test_nao_cria_segunda_locacao_ativa_no_imovel(self):
        self.nova_locacao()
        resp = self.client.post(reverse("locacao_nova"), self.dados_locacao())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "já tem uma locação ativa")
        self.assertEqual(Locacao.objects.count(), 1)

    def test_editar_locacao_nao_mexe_no_valor(self):
        loc = self.nova_locacao()
        dados = self.dados_locacao(inicio="2026-02-01", dia_vencimento="15")
        dados.pop("valor_inicial")
        resp = self.client.post(reverse("locacao_editar", args=[loc.pk]), dados)
        self.assertRedirects(resp, reverse("locacao", args=[loc.pk]), fetch_redirect_response=False)
        loc.refresh_from_db()
        self.assertEqual(loc.dia_vencimento, 15)
        self.assertEqual(loc.valores.count(), 1)

    def test_valor_novo_e_duplicado(self):
        loc = self.nova_locacao()
        url = reverse("valor_novo", args=[loc.pk])
        resp = self.client.post(url, {"vigente_desde": "2027-02-01", "valor": "1100", "motivo": "ESCALONAMENTO"})
        self.assertRedirects(resp, reverse("locacao", args=[loc.pk]), fetch_redirect_response=False)
        resp = self.client.post(url, {"vigente_desde": "2027-02-01", "valor": "1200", "motivo": "ACORDO"})
        self.assertContains(resp, "Já existe um valor")
        self.assertEqual(loc.valores.count(), 2)

    def test_encerrar_locacao_via_post(self):
        loc = self.nova_locacao()
        url = reverse("locacao_encerrar", args=[loc.pk])
        self.assertContains(self.client.get(url), "Encerrar locação")
        loc.refresh_from_db()
        self.assertTrue(loc.ativa)  # GET não encerra
        resp = self.client.post(url, {"data_saida": "2026-06-30"})
        self.assertRedirects(resp, reverse("locacao", args=[loc.pk]), fetch_redirect_response=False)
        loc.refresh_from_db()
        self.assertFalse(loc.ativa)
        self.assertEqual(loc.data_saida, date(2026, 6, 30))

    def test_encerrar_com_mes_pago_depois_da_saida_mostra_erro(self):
        loc = self.nova_locacao(inicio=date(2026, 6, 1))
        gerar_cobrancas(date(2026, 9, 2), loc)
        registrar_pagamento(loc, date(2026, 6, 5), Decimal("4000"))  # jun..set
        resp = self.client.post(reverse("locacao_encerrar", args=[loc.pk]), {"data_saida": "2026-07-31"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ago/2026")
        loc.refresh_from_db()
        self.assertTrue(loc.ativa)

    def test_editar_cadastro_tira_marca_de_importado(self):
        Imovel.objects.filter(pk=self.imovel.pk).update(importado=True)
        Inquilino.objects.filter(pk=self.inquilino.pk).update(importado=True)
        Empresa.objects.filter(pk=self.empresa.pk).update(importado=True)
        self.client.post(reverse("imovel_editar", args=[self.imovel.pk]),
                         {"codigo": "UF-001", "participacao_pct": "100", "empresa": self.empresa.pk})
        self.client.post(reverse("inquilino_editar", args=[self.inquilino.pk]), {"nome": "Inquilino Teste"})
        self.client.post(reverse("empresa_editar", args=[self.empresa.pk]), {"nome": "Empresa Teste"})
        for obj in (self.imovel, self.inquilino, self.empresa):
            obj.refresh_from_db()
            self.assertFalse(obj.importado, type(obj).__name__)

    def test_detalhe_da_locacao_lista_cobrancas_geradas(self):
        loc = self.nova_locacao(inicio=date(2026, 2, 1))
        resp = self.client.get(reverse("locacao", args=[loc.pk]))
        self.assertTrue(loc.cobrancas.exists())
        self.assertEqual(loc.cobrancas.count(), loc.cobrancas.filter(competencia__lte=timezone.localdate()).count())
        self.assertContains(resp, "fev/2026")
        self.assertContains(resp, "R$ 1.000,00")
        self.assertContains(resp, "status-atrasada")
        self.assertContains(resp, "Atrasada")

    def test_aguardando_proprietario_nao_e_divida(self):
        self.empresa.acesso_direto = False
        self.empresa.save()
        loc = self.nova_locacao()
        resp = self.client.get(reverse("locacao", args=[loc.pk]))
        self.assertContains(resp, "Aguardando: ")
        self.assertNotContains(resp, "Dívida")
        self.assertNotContains(resp, "status-atrasada")
        imovel = self.client.get(reverse("imovel", args=[self.imovel.pk]))
        self.assertContains(imovel, '<th scope="col" class="num">Aguardando</th>', html=True)

    def test_dividas_atrasadas_aparecem(self):
        loc = self.nova_locacao()
        self.assertContains(self.client.get(reverse("locacao", args=[loc.pk])), "Dívida")

    def test_formulario_de_locacao_em_grupos(self):
        nova = self.client.get(reverse("locacao_nova"), {"imovel": "abc"})
        self.assertEqual(nova.status_code, 200)
        for legenda in ("Partes", "Prazo e vencimento", "Reajuste e condições", "Contrato e observações"):
            self.assertContains(nova, f"<legend>{legenda}</legend>")
        self.assertContains(nova, "Valor inicial do aluguel")
        loc = self.nova_locacao()
        editar = self.client.get(reverse("locacao_editar", args=[loc.pk]))
        self.assertContains(editar, "<legend>Partes</legend>")
        self.assertNotContains(editar, "valor_inicial")

    def test_erro_mostra_resumo_com_links(self):
        resp = self.client.post(reverse("imovel_novo"), {"participacao_pct": "100"})
        self.assertContains(resp, 'role="alert"')
        self.assertContains(resp, 'href="#id_codigo"')

    def test_post_em_objeto_inexistente_da_404(self):
        self.assertEqual(self.client.post(reverse("despesa_informada", args=[999])).status_code, 404)
        self.assertEqual(self.client.post(reverse("tarefa_concluir", args=[999])).status_code, 404)

    def test_detalhe_do_imovel_mostra_linha_do_tempo(self):
        loc = self.nova_locacao()
        resp = self.client.get(reverse("imovel", args=[self.imovel.pk]))
        self.assertContains(resp, "Identificação")
        self.assertContains(resp, "Inquilino Teste")
        self.assertContains(resp, reverse("locacao", args=[loc.pk]))
        self.assertTrue(loc.cobrancas.exists())

    def test_despesa_informada_so_post(self):
        d = Despesa.objects.create(imovel=self.imovel, tipo="IPTU", competencia=date(2026, 3, 1), valor=Decimal("50"))
        url = reverse("despesa_informada", args=[d.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertRedirects(self.client.post(url), reverse("despesas"), fetch_redirect_response=False)
        d.refresh_from_db()
        self.assertEqual(d.informado_em, timezone.localdate())

    def test_tarefa_nova_e_concluir(self):
        resp = self.client.post(reverse("tarefa_nova"), {"titulo": "Ligar para o síndico", "prazo": "2026-11-10"})
        self.assertRedirects(resp, reverse("tarefas"), fetch_redirect_response=False)
        t = TarefaManual.objects.get()
        url = reverse("tarefa_concluir", args=[t.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        t.refresh_from_db()
        self.assertTrue(t.concluida)

    def test_config_salva(self):
        resp = self.client.post(reverse("config"), {
            "percentual_comissao": "6,5", "inicio_historico": "2026-02-01", "aviso_termino_dias": "60",
        })
        self.assertRedirects(resp, reverse("config"), fetch_redirect_response=False)
        self.assertEqual(Config.get().percentual_comissao, Decimal("6.5"))
        self.assertContains(self.client.get(reverse("config")), "Gerar cobranças automaticamente a partir de")

    def test_config_gerar_a_partir_de_so_leitura_depois_de_definido(self):
        c = Config.get()
        c.gerar_a_partir_de = date(2026, 11, 1)
        c.save()
        self.assertContains(self.client.get(reverse("config")), "disabled")
        self.client.post(reverse("config"), {
            "percentual_comissao": "5", "inicio_historico": "2026-02-01", "aviso_termino_dias": "90",
            "gerar_a_partir_de": "2026-01-01",
        })
        self.assertEqual(Config.get().gerar_a_partir_de, date(2026, 11, 1))

    def test_config_avisa_ao_mudar_corte_com_comissao_recebida(self):
        ComissaoMes.objects.create(mes=date(2026, 3, 1), recebida_em=date(2026, 4, 5))
        dados = {"percentual_comissao": "5", "inicio_historico": "2026-02-01", "aviso_termino_dias": "90"}
        resp = self.client.post(reverse("config"), dados, follow=True)
        self.assertNotContains(resp, "já marcada como recebida")
        resp = self.client.post(reverse("config"), {**dados, "mes_corte": "2026-03-01"}, follow=True)
        self.assertContains(resp, "já marcada como recebida")
        self.assertEqual(Config.get().mes_corte, date(2026, 3, 1))

    def test_editar_dia_vencimento_atualiza_cobrancas_em_aberto_do_mes_atual_em_diante(self):
        hoje = timezone.localdate()
        inicio = primeiro_dia(somar_meses(hoje, -2))
        loc = self.nova_locacao(inicio=inicio)
        gerar_cobrancas(somar_meses(hoje, 1), loc)  # passado, atual e um futuro
        registrar_pagamento(loc, hoje, Decimal("1000"), alocacoes=[(loc.cobrancas.last(), Decimal("1000"))])
        dados = self.dados_locacao(inicio=f"{inicio:%Y-%m-%d}", dia_vencimento="20")
        dados.pop("valor_inicial")
        self.client.post(reverse("locacao_editar", args=[loc.pk]), dados)
        dias = [(c.competencia, c.vencimento.day) for c in loc.cobrancas.all()]
        atual = primeiro_dia(hoje)
        self.assertEqual(dias[0], (inicio, 10))  # mês passado não muda
        self.assertIn((atual, 20), dias)
        self.assertEqual(dias[-1][1], 10)  # já tem pagamento: não muda

    def test_locacao_mostra_nome_do_mes_base(self):
        loc = self.nova_locacao()
        loc.mes_base_reajuste = 3
        loc.save()
        self.assertContains(self.client.get(reverse("locacao", args=[loc.pk])), "mês-base: março")

    def test_nav_staff(self):
        resp = self.client.get(reverse("imoveis"))
        for nome in ("tarefas", "dashboard", "imoveis", "inquilinos", "despesas", "config"):
            self.assertContains(resp, f'href="{reverse(nome)}"')

    def test_visualizacao_403_em_todas(self):
        loc = self.nova_locacao()
        despesa = Despesa.objects.create(imovel=self.imovel, tipo="IPTU", competencia=date(2026, 3, 1), valor=1)
        tarefa = TarefaManual.objects.create(titulo="Tarefa Teste")
        urls = [
            ("empresas", []), ("empresa_nova", []), ("empresa_editar", [self.empresa.pk]),
            ("imoveis", []), ("imovel_novo", []), ("imovel", [self.imovel.pk]), ("imovel_editar", [self.imovel.pk]),
            ("inquilinos", []), ("inquilino_novo", []), ("inquilino", [self.inquilino.pk]),
            ("inquilino_editar", [self.inquilino.pk]),
            ("locacao_nova", []), ("locacao", [loc.pk]), ("locacao_editar", [loc.pk]), ("valor_novo", [loc.pk]),
            ("locacao_encerrar", [loc.pk]),
            ("despesas", []), ("despesa_nova", []), ("despesa_editar", [despesa.pk]),
            ("despesa_informada", [despesa.pk]),
            ("tarefa_nova", []), ("tarefa_concluir", [tarefa.pk]), ("config", []),
        ]
        visualizacao = User.objects.create_user("visualizacao", password="senha-teste-123")
        self.client.force_login(visualizacao)
        for nome, args in urls:
            url = reverse(nome, args=args)
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url).status_code, 403)
        loc.refresh_from_db()
        self.assertTrue(loc.ativa)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("imoveis")).status_code, 302)

