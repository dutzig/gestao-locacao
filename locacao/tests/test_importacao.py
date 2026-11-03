import os
import tempfile
from datetime import date, datetime
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone
from openpyxl import Workbook

from locacao.cobrancas import gerar_cobrancas
from locacao.dinheiro import primeiro_dia, somar_meses
from locacao.importacao import importar
from locacao.models import (
    AnotacaoDivida, Cobranca, ComissaoMes, Config, Despesa, Empresa, Imovel, Inquilino, Locacao, Pagamento, Reajuste,
    RevisaoImportacao, TarefaManual, ValorAluguel,
)

IMOVEIS = ["ID Imovel", "Cidade", "Endereco", "Unidade", "Inscricao", "Incorporadora", "Imobiliaria", "Status",
           "Observacoes", "IPTU", None, None, None, None, None, None, "Energia"]
CONTRATOS = ["ID Imovel", "Inquilino", "Valor Aluguel", "Vencimento", "Banco Pagamento", "Data Inicio", "Duracao",
             "Data Termino", "Indice", "Data Proximo Reajuste", "Link Contrato PDF", "Observações", "Comissao (5%)"]
INQUILINOS = ["ID Imovel", "Inquilino (Resumo)", "Nome Locatario 1 / Razao Social", "CPF/CNPJ Locatario 1",
              "Nome Locatario 2 / Representante", "CPF/CNPJ Locatario 2", "Tipo de Garantia", "Observacoes", "Contato"]
LANCAMENTOS = ["Data Contrato", "ID Imovel", "Inquilino", "Mes/Ano Referencia", "Valor Aluguel", "Valor Recebido",
               "Data Pagamento", "Comissao (5%)", "Status", "Observacoes"]
ENERGIA = "Luz 999 " + "x" * 292  # 300 caracteres


def planilha(caminho, sem_aba_inquilinos=False, sem_status=False):
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("Imoveis")
    ws.append(IMOVEIS)
    ws.append(["UF-001", "Cidade A", "Rua Teste 1", "101", 20008805.0, "Empresa Teste", "Imob Teste", "Alugado",
               "obs imóvel", "parcela única", None, datetime(2026, 3, 10), 1234.5, None, None, None, ENERGIA])
    ws.append(["UF-002", "Cidade A", "Rua Teste 2", "202", "03123200", None, None, "Vago", None, None,
               None, None, None, None, None, None, None])
    ws.append(["UF-003", "Cidade B", "Rua Teste 3", "303", None, None, None, "Alugado", None, None,
               None, None, None, None, None, None, None])
    ws = wb.create_sheet("Contratos")
    ws.append(CONTRATOS)
    ws.append(["UF-001", "Inquilino Novo", 1000, 5.0, "BANCOX", datetime(2026, 4, 1), 30.0, datetime(2028, 10, 1),
               "IGP-M", datetime(2027, 4, 1), "https://example.com/c.pdf", 0.8, None])
    ws.append(["UF-001", "Inquilino Novo", 1000, 5.0, "BANCOX", datetime(2026, 4, 1), 30.0, None,
               None, None, None, None, None])  # contrato repetido
    ws.append(["UF-002", "Inquilino Dois", None, 10.0, "PIX EMPRESA", datetime(2025, 1, 1), 12.0, None,
               None, None, None, "texto obs", None])
    ws.append(["UF-002", "Inquilino Z", "abc", 10.0, None, datetime(2025, 1, 1), None, None,
               None, None, None, None, None])  # valor inválido
    ws.append(["UF-002", "Inquilino Z", 900, 10.0, None, datetime(2025, 1, 1), None, None,
               None, None, None, None, None])  # repete o contrato rejeitado
    ws.append(["UF-003", "Inquilino Tres", "R$ 1.500,00", 5.5, "?", None, None, None,
               "IPCA", None, "ftp://example.com/c.pdf", None, None])
    ws.append([None, "Total", 1000, None, None, None, None, None, None, None, None, None, 50])
    if not sem_aba_inquilinos:
        ws = wb.create_sheet("Inquilinos")
        ws.append(INQUILINOS)
        ws.append(["UF-001", "Inquilino Novo", "Inquilino Novo Ltda", "00.000.000/0000-00", "Representante Teste",
                   "000.000.000-00", "Caução", 12345, "contato teste"])
        ws.append(["UF-001", "Inquilino Novo", "Outro Nome", None, None, None, None, None, None])  # repetido
    ws = wb.create_sheet("Lancamentos")
    ws.append(LANCAMENTOS[:-2] + LANCAMENTOS[-1:] if sem_status else LANCAMENTOS)
    linhas = [
        [datetime(2026, 2, 10), "UF-001", "Inquilino Antigo", "fev./2026", "900,00", 900, datetime(2026, 2, 9),
         0.125, "Recebido", None],
        [datetime(2026, 3, 10), "UF-001", "Inquilino Antigo", "mar./2026", None, 500, None, 25.0, "Parcial",
         "obs parcial"],  # inquilino antigo sem valor: esperado = recebido
        [datetime(2026, 4, 5), "UF-001", "Inquilino Novo", "abr./2026", None, 1000, None, 276.9735,
         "Recebido", None],
        [datetime(2026, 4, 5), "UF-001", "Inquilino Novo", "abr./2026", None, 1000, None, 276.9735,
         "Recebido", None],  # duplicada
        [datetime(2026, 3, 5), "UF-001", "Inquilino Novo", datetime(2026, 3, 1), 1000, 1000,
         datetime(2026, 3, 6), 50, "Recebido", None],  # antes do início do contrato
        [datetime(2026, 5, 5), "UF-001", "Inquilino Novo", "mai./2026", "abc", 1000, None, None, "Recebido", None],
        [None, None, "Inquilino X", "abr./2026", None, 300, None, None, "Recebido", None],  # sem imóvel
        [datetime(2026, 5, 10), "UF-002", "Inquilino Dois", "mai./2026", None, 700, None, None, "Recebido",
         None],  # sem valor em lugar nenhum
        [datetime(2026, 6, 10), "UF-002", "Inquilino Dois", "jun./2026", None, 0, None, None, "Pendente", None],
        [datetime(2026, 6, 10), "UF-002", "Inquilino Dois", "jun./2026", 700, 700, None, None, "Recebido",
         None],  # duplicada de uma linha pulada
        [None, "UF-002", "Inquilino Z", "mai./2026", 900, 900, None, None, "Recebido", None],  # contrato rejeitado
        [None, "UF-002", "Inquilino Velho", "jan./2026", 400, 400, None, None, "Recebido", None],
        [None, None, None, None, None, None, None, None, "Pendente", None],  # só status
        [None, "UF-001", "Inquilino Novo", "xyz/2026", 1000, 0, None, None, None, None],  # referência ruim
        [None] * 10,
    ]
    for linha in linhas:
        ws.append(linha[:-2] + linha[-1:] if sem_status else linha)
    ws = wb.create_sheet("Outros")
    ws.append(["qualquer"])
    wb.save(caminho)


def temporario():
    fd, caminho = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    return caminho


class ImportacaoTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.caminho = temporario()
        planilha(cls.caminho)

    @classmethod
    def tearDownClass(cls):
        os.remove(cls.caminho)
        super().tearDownClass()

    def setUp(self):
        c = Config.get()
        c.inicio_historico = date(2026, 6, 1)
        c.save()
        self.contagens = importar(self.caminho)

    def motivos(self, aba=None):
        qs = RevisaoImportacao.objects.filter(aba=aba) if aba else RevisaoImportacao.objects.all()
        return list(qs.values_list("motivo", flat=True))

    def test_imoveis_e_empresa(self):
        self.assertEqual(Imovel.objects.count(), 3)
        a, b, c = (Imovel.objects.get(codigo=f"UF-00{i}") for i in (1, 2, 3))
        self.assertEqual(a.participacao_pct, Decimal("80"))
        self.assertEqual(b.participacao_pct, Decimal("100"))
        self.assertEqual(a.inscricao_iptu, "20008805")
        self.assertEqual(b.inscricao_iptu, "03123200")
        self.assertTrue(a.outros.startswith("IPTU: parcela única · 10/03/2026 · 1.234,5\n"))
        self.assertEqual(a.codigo_luz, ENERGIA[:200])
        self.assertIn(f"Energia: {ENERGIA}", a.outros)
        self.assertEqual(b.empresa, a.empresa)  # por cidade
        self.assertIsNone(c.empresa)
        self.assertEqual(Empresa.objects.get().banco, "BANCOX")
        motivos = self.motivos("Imoveis")
        self.assertIn("empresa atribuída pela cidade (Empresa Teste)", motivos)
        self.assertIn("imóvel sem incorporadora e sem empresa na cidade", motivos)
        self.assertTrue(any("Energia com mais de 200" in m for m in motivos))

    def test_locacoes(self):
        self.assertEqual(Locacao.objects.count(), 5)
        antiga = Locacao.objects.get(ativa=False, imovel__codigo="UF-001")
        self.assertEqual(antiga.inquilino.nome, "Inquilino Antigo")
        self.assertEqual((antiga.inicio, antiga.data_saida, antiga.dia_vencimento),
                         (date(2026, 2, 1), date(2026, 4, 1), 10))
        self.assertEqual(antiga.valor_em(date(2026, 2, 1)), Decimal("900"))
        velho = Locacao.objects.get(ativa=False, imovel__codigo="UF-002")
        self.assertEqual(velho.dia_vencimento, 1)
        nova = Locacao.objects.get(imovel__codigo="UF-001", ativa=True)
        self.assertEqual((nova.indice, nova.mes_base_reajuste, nova.duracao_meses, nova.dia_vencimento),
                         ("IGPM", 4, 30, 5))
        self.assertEqual(nova.termino, date(2028, 10, 1))
        self.assertEqual(nova.link_contrato, "https://example.com/c.pdf")
        self.assertEqual(nova.valor_em(date(2026, 4, 1)), Decimal("1000"))
        self.assertEqual(nova.inquilino.nome, "Inquilino Novo Ltda")
        self.assertEqual(nova.inquilino.documento2, "000.000.000-00")
        self.assertEqual(nova.inquilino.observacoes, "12345")
        self.assertEqual(nova.garantia, "Caução")
        dois = Locacao.objects.get(imovel__codigo="UF-002", ativa=True)
        self.assertIn("Banco: PIX EMPRESA", dois.observacoes)
        self.assertIn("texto obs", dois.observacoes)
        self.assertEqual(dois.indice, "NENHUM")
        self.assertFalse(dois.valores.exists())
        tres = Locacao.objects.get(imovel__codigo="UF-003")
        self.assertEqual(tres.inicio, timezone.localdate().replace(day=1))
        self.assertEqual(tres.dia_vencimento, 1)
        self.assertEqual(tres.link_contrato, "")
        self.assertIn("Banco: ?", tres.observacoes)
        self.assertIn("Link do contrato: ftp://example.com/c.pdf", tres.observacoes)
        self.assertEqual(tres.valor_em(tres.inicio), Decimal("1500.00"))
        self.assertFalse(Locacao.objects.filter(inquilino__nome="Inquilino Z").exists())
        motivos = self.motivos("Contratos")
        self.assertEqual(sum("contrato repetido" in m for m in motivos), 2)
        self.assertIn("lançamento de contrato não importado", self.motivos("Lancamentos"))
        for trecho in ("contrato repetido", "valor de aluguel inválido", "sem data de início",
                       "sem dia de vencimento válido", "link do contrato inválido", "contrato sem valor"):
            self.assertTrue(any(trecho in m for m in motivos), trecho)

    def test_cobrancas_e_pagamentos(self):
        self.assertEqual(Cobranca.objects.count(), 6)  # gerar_cobrancas não é chamado
        fev = Cobranca.objects.get(competencia=date(2026, 2, 1))
        self.assertEqual(fev.valor_esperado, Decimal("900.00"))
        self.assertEqual(fev.comissao_importada, Decimal("0.13"))  # HALF_UP no empate
        self.assertEqual(fev.alocacoes.get().pagamento.data, date(2026, 2, 9))
        abr = Cobranca.objects.get(locacao__inquilino__nome="Inquilino Novo Ltda", competencia=date(2026, 4, 1))
        self.assertEqual(abr.valor_esperado, Decimal("1000"))
        self.assertEqual(abr.comissao_importada, Decimal("276.97"))
        self.assertTrue(abr.quitada)
        self.assertEqual(abr.alocacoes.get().pagamento.data, None)
        mar = Cobranca.objects.get(locacao__inquilino__nome="Inquilino Novo Ltda", competencia=date(2026, 3, 1))
        self.assertEqual(mar.vencimento, date(2026, 3, 5))
        parcial = Cobranca.objects.get(locacao__ativa=False, competencia=date(2026, 3, 1))
        self.assertFalse(parcial.quitada)
        self.assertEqual(parcial.valor_esperado, Decimal("500"))  # não herda o 900 de outra linha
        self.assertEqual(parcial.recebido, Decimal("500"))
        self.assertEqual(parcial.observacoes, "obs parcial")
        mai = Cobranca.objects.get(locacao__imovel__codigo="UF-002", competencia=date(2026, 5, 1))
        self.assertEqual(mai.valor_esperado, Decimal("700"))
        self.assertFalse(Cobranca.objects.filter(competencia__in=[date(2026, 6, 1)]).exists())
        self.assertFalse(Cobranca.objects.filter(locacao__imovel__codigo="UF-001",
                                                 competencia=date(2026, 5, 1)).exists())
        self.assertEqual(Pagamento.objects.count(), 6)
        self.assertTrue(all(Pagamento.objects.values_list("importado", flat=True)))
        self.assertEqual(Config.get().inicio_historico, date(2026, 1, 1))
        self.assertEqual(Config.get().gerar_a_partir_de, date(2026, 6, 1))  # mês seguinte ao último (mai)

    def test_revisao(self):
        motivos = self.motivos("Lancamentos")
        for trecho in ("duplicado", "sem imóvel", "referência", "valor inválido", "sem data de vencimento"):
            self.assertTrue(any(trecho in m for m in motivos), trecho)
        self.assertEqual(sum("duplicado" in m for m in motivos), 2)
        self.assertEqual(sum("sem valor" in m for m in motivos), 3)
        self.assertTrue(any("Outros" in m for m in self.motivos("Outros")))
        self.assertTrue(any("inquilino repetido" in m for m in self.motivos("Inquilinos")))
        sem_imovel = RevisaoImportacao.objects.get(motivo__contains="sem imóvel")
        self.assertIn("Inquilino X", sem_imovel.dados)
        self.assertEqual(self.contagens["revisao"], RevisaoImportacao.objects.count())

    def test_reimportar_e_recusa(self):
        antes = dict(self.contagens)
        self.assertEqual(importar(self.caminho), antes)
        loc = Locacao.objects.filter(ativa=True).first()
        imovel = loc.imovel
        dados_do_usuario = [
            lambda: Pagamento.objects.create(locacao=loc, valor=Decimal("1")),
            lambda: Despesa.objects.create(imovel=imovel, tipo="IPTU", competencia=date(2026, 3, 1), valor=1),
            lambda: TarefaManual.objects.create(titulo="Tarefa Teste"),
            lambda: ComissaoMes.objects.create(mes=date(2026, 3, 1)),
            lambda: Reajuste.objects.create(locacao=loc, ano=2027, indice="IPCA", valor_atual=1,
                                            vigente_desde=date(2027, 1, 1)),
            lambda: AnotacaoDivida.objects.create(locacao=loc, data=date(2026, 3, 1), texto="nota"),
            lambda: ValorAluguel.objects.create(locacao=loc, vigente_desde=date(2030, 1, 1), valor=1,
                                                motivo="REAJUSTE"),
        ]
        for criar in dados_do_usuario:
            obj = criar()
            with self.subTest(type(obj).__name__), self.assertRaises(RuntimeError):
                importar(self.caminho)
            obj.delete()
        alteracoes = {  # (alterar, desfazer)
            "revisão resolvida": (lambda: RevisaoImportacao.objects.filter(pk=RevisaoImportacao.objects.first().pk)
                                  .update(resolvida=True), lambda: RevisaoImportacao.objects.update(resolvida=False)),
            "ajuste": (lambda: Cobranca.objects.filter(pk=Cobranca.objects.first().pk).update(ajuste=-10),
                       lambda: Cobranca.objects.update(ajuste=0)),
            "acesso direto": (lambda: Empresa.objects.update(acesso_direto=True),
                              lambda: Empresa.objects.update(acesso_direto=False)),
            "locação manual": (lambda: Locacao.objects.filter(pk=loc.pk).update(importada=False),
                               lambda: Locacao.objects.filter(pk=loc.pk).update(importada=True)),
            "imóvel manual": (lambda: Imovel.objects.create(codigo="UF-900"),
                              lambda: Imovel.objects.filter(codigo="UF-900").delete()),
            "inquilino manual": (lambda: Inquilino.objects.create(nome="Inquilino Manual"),
                                 lambda: Inquilino.objects.filter(nome="Inquilino Manual").delete()),
            "empresa manual": (lambda: Empresa.objects.create(nome="Empresa Manual"),
                               lambda: Empresa.objects.filter(nome="Empresa Manual").delete()),
            "locação editada": (lambda: Locacao.objects.filter(pk=loc.pk).update(editada=True),
                                lambda: Locacao.objects.filter(pk=loc.pk).update(editada=False)),
            "situação da dívida": (lambda: Locacao.objects.filter(pk=loc.pk).update(situacao_divida="AMIGAVEL"),
                                   lambda: Locacao.objects.filter(pk=loc.pk).update(situacao_divida="NENHUMA")),
            "desconto": (lambda: Locacao.objects.filter(pk=loc.pk).update(desconto_pontualidade=10),
                         lambda: Locacao.objects.filter(pk=loc.pk).update(desconto_pontualidade=None)),
            "imóvel editado": (lambda: Imovel.objects.filter(pk=imovel.pk).update(importado=False),
                               lambda: Imovel.objects.filter(pk=imovel.pk).update(importado=True)),
        }
        for nome, (alterar, desfazer) in alteracoes.items():
            alterar()
            with self.subTest(nome), self.assertRaises(RuntimeError):
                importar(self.caminho)
            desfazer()
        self.assertEqual(importar(self.caminho), antes)
        dados_do_usuario[0]()
        self.assertEqual(importar(self.caminho, forcar=True), antes)

    def test_marca_o_que_veio_da_importacao(self):
        self.assertTrue(all(Locacao.objects.values_list("importada", flat=True)))
        for modelo in (Imovel, Inquilino, Empresa):
            self.assertTrue(all(modelo.objects.values_list("importado", flat=True)), modelo.__name__)

    def test_planilha_fora_do_formato(self):
        caminho = temporario()
        self.addCleanup(os.remove, caminho)
        planilha(caminho, sem_aba_inquilinos=True, sem_status=True)
        with self.assertRaises(RuntimeError) as ctx:
            importar(caminho)
        self.assertIn("aba inquilinos", str(ctx.exception))
        self.assertIn("coluna 'status' na aba Lancamentos", str(ctx.exception))
        self.assertEqual(Imovel.objects.count(), 3)  # nada apagado

    def test_comando(self):
        out = StringIO()
        call_command("importar_planilha", self.caminho, stdout=out)
        self.assertIn("Itens para revisão: ", out.getvalue())
        self.assertIn("Imóveis: 3", out.getvalue())
        with self.assertRaisesMessage(CommandError, "Não foi possível abrir a planilha"):
            call_command("importar_planilha", "/caminho/que/nao/existe.xlsx", stdout=StringIO())


class LimiteGeracaoTests(TestCase):
    def test_limite_nunca_depois_do_mes_da_importacao(self):
        hoje = primeiro_dia(timezone.localdate())
        passado, futuro = somar_meses(hoje, -1), somar_meses(hoje, 1)
        inicio = datetime.combine(somar_meses(hoje, -3), datetime.min.time())
        wb = Workbook()
        wb.remove(wb.active)
        for nome, cab in (("Imoveis", IMOVEIS), ("Contratos", CONTRATOS), ("Inquilinos", INQUILINOS),
                          ("Lancamentos", LANCAMENTOS)):
            wb.create_sheet(nome).append(cab)
        for codigo in ("UF-001", "UF-002"):
            wb["Imoveis"].append([codigo, "Cidade A", None, None, None, "Empresa Teste"])
        wb["Contratos"].append(["UF-001", "Inquilino Adiantado", 1000, 5, None, inicio])
        wb["Contratos"].append(["UF-002", "Inquilino Em Dia", 800, 5, None, inicio])
        for comp in (passado, hoje, futuro):
            wb["Lancamentos"].append([None, "UF-001", "Inquilino Adiantado", comp, 1000, 1000, None, None, "Recebido"])
        wb["Lancamentos"].append([None, "UF-002", "Inquilino Em Dia", passado, 800, 800, None, None, "Recebido"])
        caminho = temporario()
        try:
            wb.save(caminho)
            importar(caminho)
        finally:
            os.remove(caminho)
        self.assertEqual(Config.get().gerar_a_partir_de, hoje)
        gerar_cobrancas(futuro)
        comps = lambda nome: list(Cobranca.objects.filter(locacao__inquilino__nome=nome)
                                  .values_list("competencia", flat=True))
        self.assertEqual(comps("Inquilino Em Dia"), [passado, hoje, futuro])
        self.assertEqual(comps("Inquilino Adiantado"), [passado, hoje, futuro])


class NomeDiferenteDoContratoTests(TestCase):
    def test_lancamentos_com_outro_nome_viram_revisao_e_contrato_respeita_limite(self):
        hoje = primeiro_dia(timezone.localdate())
        wb = Workbook()
        wb.remove(wb.active)
        for nome, cab in (("Imoveis", IMOVEIS), ("Contratos", CONTRATOS), ("Inquilinos", INQUILINOS),
                          ("Lancamentos", LANCAMENTOS)):
            wb.create_sheet(nome).append(cab)
        wb["Imoveis"].append(["UF-001", "Cidade A", None, None, None, "Empresa Teste"])
        wb["Contratos"].append(["UF-001", "Inquilino Teste Silva", 1000, 5, None,
                                datetime.combine(somar_meses(hoje, -22), datetime.min.time())])
        for n in range(9, 0, -1):
            comp = somar_meses(hoje, -n)
            wb["Lancamentos"].append([None, "UF-001", "Inquilino T. Silva", comp, 1000, 1000, None, 50, "Recebido"])
        caminho = temporario()
        try:
            wb.save(caminho)
            importar(caminho)
        finally:
            os.remove(caminho)
        self.assertEqual(RevisaoImportacao.objects.filter(
            motivo="inquilino sem contrato — conferir se é o mesmo do contrato").count(), 1)
        Empresa.objects.update(acesso_direto=True)
        gerar_cobrancas(hoje)
        contrato = Locacao.objects.get(ativa=True)
        self.assertTrue(contrato.importada)
        self.assertEqual(list(contrato.cobrancas.values_list("competencia", flat=True)), [hoje])  # sem dívida falsa
