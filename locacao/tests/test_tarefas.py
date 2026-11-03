from datetime import date
from decimal import Decimal

from django.test import TestCase

from locacao.cobrancas import registrar_pagamento
from locacao.models import (
    Cobranca, ComissaoMes, Config, Despesa, Empresa, Imovel, Inquilino, Locacao, Reajuste, RevisaoImportacao, TarefaManual,
    ValorAluguel,
)
from locacao.tarefas import listar

HOJE = date(2026, 11, 2)
LINK = "https://exemplo.test/contrato"


class TarefasTests(TestCase):
    def setUp(self):
        self.inquilino = Inquilino.objects.create(nome="Inquilino Teste", documento="000.000.000-00")
        self.n = 0

    def nova_locacao(self, inicio=date(2026, 11, 1), acesso=True, link=LINK, termino=None, mes_base=None, **kw):
        self.n += 1
        empresa = Empresa.objects.create(nome=f"Empresa Teste {self.n}", acesso_direto=acesso)
        imovel = Imovel.objects.create(codigo=f"UF-00{self.n}", empresa=empresa)
        loc = Locacao.objects.create(
            imovel=imovel, inquilino=self.inquilino, inicio=inicio, dia_vencimento=10, indice="IGPM",
            link_contrato=link, termino=termino, mes_base_reajuste=mes_base, **kw,
        )
        ValorAluguel.objects.create(locacao=loc, vigente_desde=inicio, valor=Decimal("100"), motivo="INICIAL")
        return loc

    def itens(self, tipo, hoje=HOJE):
        return [i for i in listar(hoje) if i.tipo == tipo]

    def um(self, tipo, prioridade, hoje=HOJE):
        itens = self.itens(tipo, hoje)
        self.assertEqual(len(itens), 1, itens)
        self.assertEqual(itens[0].prioridade, prioridade)
        return itens[0]

    def reajuste(self, **kw):
        loc = self.nova_locacao()
        return Reajuste.objects.create(
            locacao=loc, ano=2026, indice="IGPM", valor_atual=Decimal("100"), vigente_desde=date(2026, 11, 1), **kw,
        )

    def test_vazio(self):
        self.assertEqual(listar(HOJE), [])

    def test_atrasada(self):
        loc = self.nova_locacao(inicio=date(2026, 10, 1))
        item = self.um("ATRASADA", 1)
        self.assertEqual(item.titulo, "UF-001 — aluguel de out/2026 atrasado")
        self.assertEqual(item.url, f"/locacoes/{loc.pk}/")
        self.assertEqual(self.itens("AGUARDANDO"), [])

    def test_atrasada_gera_cobrancas(self):
        self.nova_locacao(inicio=date(2026, 9, 1))
        self.assertEqual(len(self.itens("ATRASADA")), 2)  # set e out; nov ainda não venceu

    def test_paga_nao_aparece(self):
        loc = self.nova_locacao(inicio=date(2026, 10, 1))
        listar(HOJE)
        registrar_pagamento(loc, date(2026, 10, 9), Decimal("100"))
        self.assertEqual(self.itens("ATRASADA"), [])

    def test_aguardando_agrupado(self):
        for _ in range(3):
            self.nova_locacao(inicio=date(2026, 10, 1), acesso=False)
        item = self.um("AGUARDANDO", 1)
        self.assertEqual(item.url, "/aguardando/")
        for codigo in ("UF-001", "UF-002", "UF-003"):
            self.assertIn(f"{codigo} out/2026 (23 dias)", item.detalhe)
        self.assertEqual(self.itens("ATRASADA"), [])

    def test_parcial_vencida_com_acesso(self):
        loc = self.nova_locacao(inicio=date(2026, 10, 1))
        listar(HOJE)
        registrar_pagamento(loc, date(2026, 10, 9), Decimal("50"))
        item = self.um("ATRASADA", 1)
        self.assertIn("pago parcial, falta R$ 50,00", item.detalhe)

    def test_parcial_vencida_sem_acesso(self):
        self.nova_locacao(inicio=date(2026, 10, 1), acesso=False)
        loc = self.nova_locacao(inicio=date(2026, 10, 1), acesso=False)
        listar(HOJE)
        registrar_pagamento(loc, date(2026, 10, 9), Decimal("50"))
        item = self.um("AGUARDANDO", 1)
        self.assertIn("UF-001 out/2026 (23 dias)", item.detalhe)
        self.assertIn("UF-002 out/2026 (23 dias, pago parcial, falta R$ 50,00)", item.detalhe)
        self.assertEqual(self.itens("ATRASADA"), [])

    def test_reajuste_calcular(self):
        self.nova_locacao(inicio=date(2025, 11, 1), mes_base=11)
        item = self.um("REAJUSTE_CALCULAR", 2)
        self.assertEqual(item.url, "/reajustes/?ano=2026")
        self.assertIn("UF-001", item.titulo)

    def test_reajuste_enviar(self):
        self.reajuste(percentual=Decimal("1"), valor_calculado=Decimal("101"))
        self.um("REAJUSTE_ENVIAR", 2)
        self.assertEqual(self.itens("REAJUSTE_DECISAO"), [])

    def test_reajuste_decisao(self):
        self.reajuste(valor_calculado=Decimal("101"), enviado_em=date(2026, 11, 1))
        self.um("REAJUSTE_DECISAO", 2)
        self.assertEqual(self.itens("REAJUSTE_ENVIAR"), [])

    def test_reajuste_aplicar(self):
        r = self.reajuste(valor_calculado=Decimal("101"), enviado_em=date(2026, 11, 1), decisao="APROVADO",
                          valor_aprovado=Decimal("101"))
        self.um("REAJUSTE_APLICAR", 2)
        self.assertEqual(self.itens("REAJUSTE_DECISAO"), [])
        r.aplicado_em = HOJE
        r.save()
        self.assertEqual(self.itens("REAJUSTE_APLICAR"), [])

    def test_reajuste_recusado_some(self):
        self.reajuste(valor_calculado=Decimal("101"), enviado_em=date(2026, 11, 1), decisao="RECUSADO")
        self.assertEqual([i for i in listar(HOJE) if i.tipo.startswith("REAJUSTE")], [])

    def test_contrato_vencido(self):
        loc = self.nova_locacao(termino=date(2026, 11, 1))
        item = self.um("CONTRATO_VENCIDO", 2)
        self.assertEqual(item.url, f"/locacoes/{loc.pk}/")
        self.assertEqual(self.itens("CONTRATO_TERMINANDO"), [])

    def test_contrato_terminando(self):
        self.nova_locacao(termino=HOJE)  # hoje ainda não venceu
        self.nova_locacao(termino=date(2027, 1, 31))  # hoje + 90
        self.nova_locacao(termino=date(2027, 2, 1))  # fora da janela
        itens = self.itens("CONTRATO_TERMINANDO")
        self.assertEqual(sorted(i.titulo.split(" ")[0] for i in itens), ["UF-001", "UF-002"])
        self.assertEqual({i.prioridade for i in itens}, {3})
        self.assertEqual(self.itens("CONTRATO_VENCIDO"), [])

    def test_terminando_janela_configuravel(self):
        c = Config.get()
        c.aviso_termino_dias = 10
        c.save()
        self.nova_locacao(termino=date(2026, 11, 20))
        self.assertEqual(self.itens("CONTRATO_TERMINANDO"), [])

    def test_sem_contrato(self):
        loc = self.nova_locacao(link="")
        self.nova_locacao(link="", ativa=False)
        item = self.um("SEM_CONTRATO", 3)
        self.assertEqual(item.url, f"/locacoes/{loc.pk}/")

    def test_despesa_informar(self):
        vago = Imovel.objects.create(codigo="UF-100")
        Despesa.objects.create(imovel=vago, tipo="IPTU", competencia=date(2026, 10, 1), valor=Decimal("50"))
        Despesa.objects.create(imovel=vago, tipo="AGUA", competencia=date(2026, 10, 1), valor=Decimal("20"),
                               informado_em=HOJE)
        ocupado = self.nova_locacao().imovel
        Despesa.objects.create(imovel=ocupado, tipo="IPTU", competencia=date(2026, 10, 1), valor=Decimal("50"))
        item = self.um("DESPESA_INFORMAR", 2)
        self.assertEqual(item.url, "/despesas/")
        self.assertIn("UF-100", item.titulo)

    def test_transferencia(self):
        e = Empresa.objects.create(nome="Empresa Teste")
        Imovel.objects.create(codigo="UF-101", empresa=e, proprietario_atual="Outra Pessoa Teste")
        Imovel.objects.create(codigo="UF-102", empresa=e, proprietario_atual="  empresa teste ")
        Imovel.objects.create(codigo="UF-103", empresa=e)
        Imovel.objects.create(codigo="UF-104", proprietario_atual="Outra Pessoa Teste")  # sem empresa
        item = self.um("TRANSFERENCIA", 4)
        self.assertIn("UF-101", item.titulo)
        imovel = Imovel.objects.get(codigo="UF-101")
        self.assertEqual(item.url, f"/imoveis/{imovel.pk}/")

    def test_comissao(self):
        loc = self.nova_locacao(inicio=date(2026, 9, 1))
        registrar_pagamento(loc, date(2026, 9, 8), Decimal("200"))  # set e out
        registrar_pagamento(loc, date(2026, 11, 1), Decimal("100"))  # nov: mês aberto, não entra
        ComissaoMes.objects.create(mes=date(2026, 9, 1), recebida_em=date(2026, 10, 5))
        ComissaoMes.objects.create(mes=date(2026, 10, 1))  # sem recebida_em: continua pendente
        item = self.um("COMISSAO", 2)
        self.assertIn("out/2026", item.titulo)
        self.assertEqual(item.url, "/comissoes/")

    def test_comissao_regra_nova_no_mes_do_pagamento(self):
        c = Config.get()
        c.mes_corte = date(2026, 9, 1)
        c.save()
        loc = self.nova_locacao(inicio=date(2026, 9, 1))
        registrar_pagamento(loc, date(2026, 10, 5), Decimal("100"))  # paga set em out
        item = self.um("COMISSAO", 2)
        self.assertIn("out/2026", item.titulo)

    def test_comissao_ignora_antes_do_historico(self):
        c = Config.get()
        c.inicio_historico = date(2026, 9, 1)
        c.save()
        loc = self.nova_locacao(inicio=date(2026, 9, 1))
        Cobranca.objects.create(locacao=loc, competencia=date(2026, 8, 1), vencimento=date(2026, 8, 10),
                                valor_esperado=Decimal("100"), quitada=True, comissao_importada=Decimal("5"))
        self.assertEqual(self.itens("COMISSAO"), [])
        c.inicio_historico = date(2026, 8, 1)
        c.save()
        self.assertIn("ago/2026", self.um("COMISSAO", 2).titulo)

    def test_manual(self):
        imovel = Imovel.objects.create(codigo="UF-200")
        TarefaManual.objects.create(titulo="Cobrar contrato do corretor", prazo=date(2026, 11, 1), imovel=imovel)
        TarefaManual.objects.create(titulo="Ligar para o síndico", prazo=HOJE)
        TarefaManual.objects.create(titulo="Sem prazo")
        TarefaManual.objects.create(titulo="Feita", concluida=True)
        itens = {i.titulo: i for i in self.itens("MANUAL")}
        self.assertEqual(set(itens), {"Cobrar contrato do corretor", "Ligar para o síndico", "Sem prazo"})
        self.assertEqual(itens["Cobrar contrato do corretor"].prioridade, 1)
        self.assertIn("UF-200", itens["Cobrar contrato do corretor"].detalhe)
        self.assertEqual(itens["Ligar para o síndico"].prioridade, 3)
        self.assertEqual(itens["Sem prazo"].prioridade, 3)
        self.assertEqual(itens["Sem prazo"].url, "/tarefas/")

    def test_revisao(self):
        RevisaoImportacao.objects.create(aba="Lançamentos", linha=2, motivo="sem imóvel")
        RevisaoImportacao.objects.create(aba="Lançamentos", linha=3, motivo="sem imóvel")
        RevisaoImportacao.objects.create(aba="Lançamentos", linha=4, motivo="sem imóvel", resolvida=True)
        item = self.um("REVISAO", 2)
        self.assertEqual(item.url, "/importacao/revisao/")
        self.assertIn("2", item.titulo)

    def test_empresa_acesso(self):
        Empresa.objects.create(nome="Empresa Sem Acesso")
        item = self.um("EMPRESA_ACESSO", 2)
        self.assertEqual(item.titulo, "Indique quais empresas têm acesso direto à conta")
        self.assertEqual(item.url, "/empresas/")
        Empresa.objects.create(nome="Empresa Com Acesso", acesso_direto=True)
        self.assertEqual(self.itens("EMPRESA_ACESSO"), [])

    def test_ordem(self):  # por prioridade; dentro dela, ordem da fonte (manuais: prazo, depois criação)
        TarefaManual.objects.create(titulo="B sem prazo")
        TarefaManual.objects.create(titulo="A sem prazo")
        TarefaManual.objects.create(titulo="Z vencida", prazo=date(2026, 10, 1))
        RevisaoImportacao.objects.create(aba="Imóveis", linha=2, motivo="x")
        self.assertEqual(
            [(i.prioridade, i.titulo) for i in listar(HOJE)],
            [(1, "Z vencida"), (2, "1 linha da importação para revisar"), (3, "B sem prazo"), (3, "A sem prazo")],
        )

    def test_encerrada_sai_das_atrasadas_e_vira_item_de_divida(self):
        from locacao.cobrancas import encerrar_locacao
        encerrada = self.nova_locacao(inicio=date(2026, 9, 1))
        listar(HOJE)
        encerrar_locacao(encerrada, date(2026, 10, 31))
        juridico = self.nova_locacao(inicio=date(2026, 10, 1), situacao_divida="JURIDICO")
        amigavel = self.nova_locacao(inicio=date(2026, 10, 1), situacao_divida="AMIGAVEL")
        self.assertEqual([i.titulo for i in self.itens("ATRASADA")], ["UF-003 — aluguel de out/2026 atrasado"])
        item = self.um("DIVIDA", 2)
        self.assertEqual((item.titulo, item.url), ("Dívidas de locações encerradas (1)", "/dividas/"))
        encerrada.situacao_divida = "BAIXADA"
        encerrada.save()
        self.assertEqual(self.itens("DIVIDA"), [])
        self.assertTrue(juridico and amigavel)

    def test_encerrada_sem_acesso_sai_do_aguardando(self):
        from locacao.cobrancas import encerrar_locacao
        loc = self.nova_locacao(inicio=date(2026, 10, 1), acesso=False)
        listar(HOJE)
        encerrar_locacao(loc, date(2026, 10, 31))
        self.assertEqual(self.itens("AGUARDANDO"), [])
        self.assertEqual(len(self.itens("DIVIDA")), 1)

    def test_ativa_quitada_ou_baixada_nao_esconde_mes_novo_atrasado(self):
        for situacao in ("QUITADA", "BAIXADA"):
            self.nova_locacao(inicio=date(2026, 10, 1), situacao_divida=situacao)
        self.assertEqual(len(self.itens("ATRASADA")), 2)

    def test_dividas_de_ativa_so_vencidas(self):
        from locacao.tarefas import dividas
        self.nova_locacao(inicio=date(2026, 10, 1), situacao_divida="AMIGAVEL")
        listar(HOJE)  # gera out e nov
        self.assertEqual([c.competencia for c in dividas(HOJE)], [date(2026, 10, 1)])
