from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from locacao.cobrancas import gerar_cobrancas, registrar_pagamento
from locacao.models import Config, Empresa, Imovel, Inquilino, Locacao, ValorAluguel
from locacao.templatetags.br import mes_br

FEV = date(2026, 2, 1)


class DashboardTests(TestCase):
    """Cenário: UF-001 (1.000, pago em março), UF-002 (500, empresa sem acesso), UF-003 (250, em aberto)."""

    def setUp(self):
        self.admin = User.objects.create_user("admin", password="senha-teste-123", is_staff=True)
        self.visualizacao = User.objects.create_user("visualizacao", password="senha-teste-123")
        self.client.force_login(self.admin)
        inquilino = Inquilino.objects.create(nome="Inquilino Teste", documento="000.000.000-00")
        self.locs = []
        for codigo, valor, acesso in (("UF-001", "1000", True), ("UF-002", "500", False), ("UF-003", "250", True)):
            empresa = Empresa.objects.create(nome=f"Empresa {codigo}", acesso_direto=acesso)
            imovel = Imovel.objects.create(codigo=codigo, empresa=empresa)
            loc = Locacao.objects.create(imovel=imovel, inquilino=inquilino, inicio=FEV, dia_vencimento=10, indice="IGPM")
            ValorAluguel.objects.create(locacao=loc, vigente_desde=FEV, valor=Decimal(valor), motivo="INICIAL")
            gerar_cobrancas(date(2026, 6, 30), loc)
            self.locs.append(loc)
        registrar_pagamento(self.locs[0], date(2026, 3, 8), Decimal("1000"))  # quita fevereiro

    def corte(self, mes):
        c = Config.get()
        c.mes_corte = mes
        c.save()

    def test_cartoes_regra_antiga(self):
        resp = self.client.get(reverse("dashboard"), {"mes": "2026-02"})
        r = resp.context["r"]
        self.assertEqual(r.mes, FEV)
        self.assertEqual((r.esperada, r.realizada, r.pendente_atrasado, r.pendente_aguardando, r.comissao),
                         (Decimal("1750"), Decimal("1000"), Decimal("250"), Decimal("500"), Decimal("50.00")))
        self.assertContains(resp, "Dashboard — fev/2026")
        self.assertContains(resp, '<dt>Receita esperada</dt><dd class="cartao-valor">R$ 1.750,00</dd>', html=True)
        self.assertContains(resp, '<dt>Receita realizada</dt><dd class="cartao-valor">R$ 1.000,00</dd>'
                                  '<dd class="cartao-detalhe">por mês de referência</dd>', html=True)
        self.assertContains(resp, '<dt>Pendente</dt><dd class="cartao-valor">R$ 750,00</dd><dd class="cartao-detalhe">'
                                  'das cobranças deste mês: em aberto ou atrasado R$ 250,00 · '
                                  'aguardando R$ 500,00</dd>', html=True)
        self.assertContains(resp, '<dt>Comissão</dt><dd class="cartao-valor">R$ 50,00</dd>'
                                  '<dd class="cartao-detalhe">por mês de referência</dd>', html=True)
        self.assertNotContains(resp, "Pagamentos do mês")
        self.assertContains(resp, '<span class="status status-aguardando">Aguardando</span>', html=True)
        self.assertContains(resp, "Atrasada")
        self.assertContains(resp, 'href="?mes=2026-01"')
        self.assertContains(resp, 'href="?mes=2026-03"')

    def test_regra_nova_lista_pagamentos(self):
        self.corte(date(2026, 3, 1))
        registrar_pagamento(self.locs[2], date(2026, 4, 5), Decimal("500"))  # quita fev e mar da UF-003
        resp = self.client.get(reverse("dashboard"), {"mes": "2026-04"})
        r = resp.context["r"]
        self.assertTrue(r.regra_nova)
        self.assertEqual((r.realizada, r.comissao), (Decimal("500"), Decimal("25.00")))
        self.assertEqual([pl.competencias for pl in r.pagamentos], [[FEV, date(2026, 3, 1)]])
        self.assertContains(resp, '<dt>Receita realizada</dt><dd class="cartao-valor">R$ 500,00</dd>'
                                  '<dd class="cartao-detalhe">por data de pagamento</dd>', html=True)
        self.assertContains(resp, '<dt>Comissão</dt><dd class="cartao-valor">R$ 25,00</dd>'
                                  '<dd class="cartao-detalhe">por data de pagamento</dd>', html=True)
        self.assertContains(resp, "<td>05/04/2026</td>", html=True)
        self.assertContains(resp, "<td>fev/2026, mar/2026</td>", html=True)
        html = resp.content.decode()
        self.assertLess(html.index("Pagamentos do mês"), html.index("<h2>Recebidos</h2>"))

    def test_parcial_vencida_sem_acesso_aparece_aguardando(self):
        registrar_pagamento(self.locs[1], date(2026, 3, 8), Decimal("200"))  # parcial de fev da UF-002
        resp = self.client.get(reverse("dashboard"), {"mes": "2026-02"})
        r = resp.context["r"]
        self.assertEqual((r.pendente_atrasado, r.pendente_aguardando), (Decimal("250"), Decimal("300")))
        self.assertContains(resp, '<span class="status status-aguardando">Aguardando (parcial)</span>',
                            html=True)

    def test_tabelas_vazias(self):
        resp = self.client.get(reverse("dashboard"), {"mes": "2025-01"})
        self.assertContains(resp, "Nenhuma cobrança pendente")
        self.assertContains(resp, "Nenhum recebimento")

    def test_mes_invalido_usa_mes_atual(self):
        hoje = timezone.localdate()
        for valor in ("xyz", "2026-13", "0001-01"):
            resp = self.client.get(reverse("dashboard"), {"mes": valor})
            self.assertEqual(resp.context["r"].mes, hoje.replace(day=1))
            self.assertContains(resp, f"Dashboard — {mes_br(hoje)}")

    def test_staff_tem_link_para_locacao(self):
        resp = self.client.get(reverse("dashboard"), {"mes": "2026-02"})
        self.assertContains(resp, f'href="{reverse("locacao", args=[self.locs[1].pk])}"')
        self.assertContains(resp, f'href="{reverse("periodo")}"')

    def test_periodo_soma_semestre(self):
        resp = self.client.get(reverse("periodo"), {"ano": "2026", "semestre": "1"})
        # fev..jun: jan fica de fora (antes do início do histórico)
        self.assertEqual([r.mes.month for r in resp.context["resumos"]], [2, 3, 4, 5, 6])
        self.assertEqual(resp.context["total"], {"esperada": Decimal("8750"), "realizada": Decimal("1000"),
                                                 "pendente_total": Decimal("7750"), "comissao": Decimal("50.00")})
        self.assertContains(resp, '<tr class="linha-total"><th scope="row">Total</th><td class="num">R$ 8.750,00</td>'
                                  '<td class="num">R$ 1.000,00</td><td class="num">R$ 7.750,00</td>'
                                  '<td class="num">R$ 50,00</td></tr>', html=True)
        self.assertContains(resp, f'href="{reverse("dashboard")}?mes=2026-03"')

    def test_periodo_invalido_usa_ano_atual(self):
        hoje = timezone.localdate()
        resp = self.client.get(reverse("periodo"), {"ano": "abc", "semestre": "3"})
        self.assertEqual(resp.context["ano"], hoje.year)
        self.assertIsNone(resp.context["semestre"])

    def test_visualizacao_ve_ambos_sem_links_admin(self):
        self.client.force_login(self.visualizacao)
        for url, params in ((reverse("dashboard"), {"mes": "2026-02"}), (reverse("periodo"), {"ano": "2026"})):
            resp = self.client.get(url, params)
            self.assertEqual(resp.status_code, 200)
            self.assertNotContains(resp, "/locacoes/")
            self.assertNotContains(resp, 'href="/imoveis/"')
            self.assertNotContains(resp, "Registrar pagamento")
            self.assertNotContains(resp, "/cobrancas/")
            self.assertContains(resp, f'href="{reverse("periodo")}"')
        self.assertContains(self.client.get(reverse("dashboard"), {"mes": "2026-02"}), "<td>UF-002</td>", html=True)

    def test_visualizacao_gera_cobrancas_do_mes_atual(self):
        self.client.force_login(self.visualizacao)
        mes_atual = timezone.localdate().replace(day=1)
        self.assertFalse(self.locs[0].cobrancas.filter(competencia=mes_atual).exists())
        self.client.get(reverse("dashboard"))
        self.assertTrue(self.locs[0].cobrancas.filter(competencia=mes_atual).exists())
