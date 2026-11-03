import io
import json
from datetime import date
from decimal import Decimal
from unittest import mock
from urllib.error import URLError

from django.test import TestCase

from locacao.cobrancas import gerar_cobrancas, registrar_pagamento
from locacao.indices import (
    IndiceIndisponivel, aplicar, buscar_variacoes, calcular_reajuste, decidir, janela, marcar_enviado,
    reajustes_devidos,
)
from locacao.models import Config, Imovel, Inquilino, Locacao, ValorAluguel


def falso(variacoes):
    return lambda indice, inicio, fim: [Decimal(v) for v in variacoes]


class IndicesTests(TestCase):
    def setUp(self):
        c = Config.get()
        c.inicio_historico = date(2026, 2, 1)
        c.save()
        self.inquilino = Inquilino.objects.create(nome="Inquilino Teste", documento="000.000.000-00")
        self.n = 0

    def nova_locacao(self, inicio=date(2026, 3, 1), valor="1000", mes_base=3, indice="IGPM"):
        self.n += 1
        imovel = Imovel.objects.create(codigo=f"UF-00{self.n}")
        loc = Locacao.objects.create(
            imovel=imovel, inquilino=self.inquilino, inicio=inicio, dia_vencimento=10,
            indice=indice, mes_base_reajuste=mes_base,
        )
        ValorAluguel.objects.create(locacao=loc, vigente_desde=inicio, valor=Decimal(valor), motivo="INICIAL")
        return loc

    def test_janela(self):
        self.assertEqual(janela(3, 2027), (date(2026, 3, 1), date(2027, 2, 1)))
        self.assertEqual(janela(1, 2027), (date(2026, 1, 1), date(2026, 12, 1)))

    def test_calculo_multiplica_fatores(self):
        r = calcular_reajuste(self.nova_locacao(), 2027, buscar=falso(["1"] * 12))
        self.assertEqual(r.percentual, Decimal("12.6825"))
        self.assertEqual(r.valor_atual, Decimal("1000"))
        self.assertEqual(r.valor_calculado, Decimal("1126.83"))
        self.assertEqual(r.vigente_desde, date(2027, 3, 1))
        self.assertEqual(r.indice, "IGPM")

    def test_buscar_recebe_janela(self):
        chamadas = []

        def buscar(indice, inicio, fim):
            chamadas.append((indice, inicio, fim))
            return [Decimal("0")] * 12

        calcular_reajuste(self.nova_locacao(indice="IPCA"), 2027, buscar=buscar)
        self.assertEqual(chamadas, [("IPCA", date(2026, 3, 1), date(2027, 2, 1))])

    def test_percentual_manual_quantizado(self):
        r = calcular_reajuste(self.nova_locacao(), 2027, percentual_manual=Decimal("2.123456"))
        self.assertEqual(r.percentual, Decimal("2.1235"))
        self.assertEqual(r.valor_calculado, Decimal("1021.24"))  # 1000 * 1.021235 = 1021.235

    def test_percentual_manual(self):
        def nao_chamar(*a):
            raise AssertionError("não deve buscar")

        r = calcular_reajuste(self.nova_locacao(), 2027, percentual_manual=Decimal("2.5"), buscar=nao_chamar)
        self.assertEqual(r.valor_calculado, Decimal("1025.00"))

    def test_indice_negativo(self):
        r = calcular_reajuste(self.nova_locacao(), 2027, buscar=falso(["-0.5"] * 12))
        self.assertLess(r.percentual, 0)
        self.assertLess(r.valor_calculado, r.valor_atual)
        self.assertEqual(r.decisao, "PENDENTE")

    def test_recalcular_atualiza_mesmo_registro(self):
        loc = self.nova_locacao()
        calcular_reajuste(loc, 2027, buscar=falso(["1"] * 12))
        r = calcular_reajuste(loc, 2027, percentual_manual=Decimal("3"))
        self.assertEqual(loc.reajustes.count(), 1)
        self.assertEqual(r.valor_calculado, Decimal("1030.00"))

    def test_fluxo_aplicar(self):
        loc = self.nova_locacao()
        gerar_cobrancas(date(2027, 4, 15), loc)
        mar, abr = loc.cobrancas.get(competencia=date(2027, 3, 1)), loc.cobrancas.get(competencia=date(2027, 4, 1))
        fev = loc.cobrancas.get(competencia=date(2027, 2, 1))  # aberta, sem alocação, antes do reajuste
        mar.quitada = True
        mar.save()

        r = calcular_reajuste(loc, 2027, buscar=falso(["1"] * 12))
        marcar_enviado(r, date(2027, 3, 2))
        decidir(r, True, date(2027, 3, 5), Decimal("1100"))
        aplicar(r, date(2027, 3, 6))

        r.refresh_from_db()
        self.assertEqual((r.enviado_em, r.decidido_em, r.aplicado_em),
                         (date(2027, 3, 2), date(2027, 3, 5), date(2027, 3, 6)))
        self.assertEqual(r.decisao, "APROVADO")
        self.assertEqual(loc.valor_em(r.vigente_desde), Decimal("1100"))
        self.assertTrue(loc.valores.filter(vigente_desde=date(2027, 3, 1), motivo="REAJUSTE").exists())
        for c in (mar, abr, fev):
            c.refresh_from_db()
        self.assertEqual(abr.valor_esperado, Decimal("1100"))
        self.assertEqual(mar.valor_esperado, Decimal("1000"))  # quitada: intocada
        self.assertEqual(fev.valor_esperado, Decimal("1000"))  # antes do reajuste
        self.assertFalse(fev.quitada)
        self.assertFalse(fev.alocacoes.exists())

    def test_aplicar_pula_isencao(self):
        loc = self.nova_locacao()
        loc.isencao_meses = 13  # isento mar/2026..mar/2027
        loc.save()
        gerar_cobrancas(date(2027, 4, 15), loc)
        r = calcular_reajuste(loc, 2027, percentual_manual=Decimal("10"))
        decidir(r, True, date(2027, 3, 5))
        aplicar(r, date(2027, 3, 6))
        self.assertEqual(loc.cobrancas.get(competencia=date(2027, 3, 1)).valor_esperado, Decimal("0"))
        self.assertEqual(loc.cobrancas.get(competencia=date(2027, 4, 1)).valor_esperado, Decimal("1100.00"))

    def test_aplicar_nao_mexe_em_cobranca_parcial(self):
        loc = self.nova_locacao()
        gerar_cobrancas(date(2027, 3, 15), loc)
        registrar_pagamento(loc, date(2027, 3, 10), Decimal("1000") * 12 + Decimal("100"))
        mar = loc.cobrancas.get(competencia=date(2027, 3, 1))
        self.assertFalse(mar.quitada)
        r = calcular_reajuste(loc, 2027, percentual_manual=Decimal("10"))
        decidir(r, True, date(2027, 3, 5))
        self.assertEqual(r.valor_aprovado, Decimal("1100.00"))
        aplicar(r, date(2027, 3, 6))
        mar.refresh_from_db()
        self.assertEqual(mar.valor_esperado, Decimal("1000"))

    def test_aplicar_com_valor_ja_cadastrado_na_data(self):
        loc = self.nova_locacao()
        ValorAluguel.objects.create(locacao=loc, vigente_desde=date(2027, 3, 1), valor=Decimal("1050"), motivo="ACORDO")
        r = calcular_reajuste(loc, 2027, percentual_manual=Decimal("10"))
        decidir(r, True, date(2027, 3, 5))
        with self.assertRaisesMessage(ValueError, "Já existe um valor do aluguel em 01/03/2027"):
            aplicar(r, date(2027, 3, 6))
        r.refresh_from_db()
        self.assertIsNone(r.aplicado_em)

    def test_recusado_nao_aplica(self):
        loc = self.nova_locacao()
        r = calcular_reajuste(loc, 2027, buscar=falso(["1"] * 12))
        decidir(r, False, date(2027, 3, 5))
        r.refresh_from_db()
        self.assertEqual((r.decisao, r.decidido_em), ("RECUSADO", date(2027, 3, 5)))
        with self.assertRaises(ValueError):
            aplicar(r, date(2027, 3, 6))
        self.assertIsNone(r.aplicado_em)
        self.assertEqual(loc.valor_em(date(2027, 3, 1)), Decimal("1000"))

    def test_pendente_nao_aplica(self):
        r = calcular_reajuste(self.nova_locacao(), 2027, buscar=falso(["1"] * 12))
        with self.assertRaises(ValueError):
            aplicar(r, date(2027, 3, 6))

    def test_devidos_ignora_escalonamento(self):
        normal = self.nova_locacao()
        escalonada = self.nova_locacao()
        ValorAluguel.objects.create(locacao=escalonada, vigente_desde=date(2027, 3, 1), valor=Decimal("1200"),
                                    motivo="ESCALONAMENTO")
        self.assertEqual(reajustes_devidos(date(2027, 3, 1)), [(normal, 2027)])

    def test_devidos_filtros(self):
        self.nova_locacao()                                 # devido
        self.nova_locacao(indice="NENHUM")                  # sem índice
        self.nova_locacao(mes_base=None)                    # sem mês-base
        self.nova_locacao(inicio=date(2026, 4, 1))          # menos de 12 meses
        self.nova_locacao(mes_base=4)                       # mês-base ainda não chegou
        inativa = self.nova_locacao()
        inativa.ativa = False
        inativa.save()
        ja = self.nova_locacao()
        calcular_reajuste(ja, 2027, percentual_manual=Decimal("1"))
        devidos = reajustes_devidos(date(2027, 3, 20))
        self.assertEqual([(l.imovel.codigo, a) for l, a in devidos], [("UF-001", 2027)])
        self.assertEqual(reajustes_devidos(date(2027, 2, 28)), [])

    def test_devidos_mes_base_do_fim_do_ano_continua_em_janeiro(self):
        dez = self.nova_locacao(inicio=date(2025, 12, 1), mes_base=12)
        self.assertEqual(reajustes_devidos(date(2026, 12, 15)), [(dez, 2026)])
        self.assertEqual(reajustes_devidos(date(2027, 1, 5)), [(dez, 2026)])
        calcular_reajuste(dez, 2026, percentual_manual=Decimal("1"))
        self.assertEqual(reajustes_devidos(date(2027, 1, 5)), [])

    def test_devidos_ano_anterior_respeita_escalonamento_e_inicio_da_geracao(self):
        escalonada = self.nova_locacao(inicio=date(2025, 12, 1), mes_base=12)
        ValorAluguel.objects.create(locacao=escalonada, vigente_desde=date(2026, 12, 1), valor=Decimal("1200"),
                                    motivo="ESCALONAMENTO")
        importada = self.nova_locacao(inicio=date(2024, 6, 1), mes_base=6)
        c = Config.get()
        c.gerar_a_partir_de = date(2026, 11, 1)  # reajuste de jun/2026 ficou com a planilha
        c.save()
        self.assertEqual(reajustes_devidos(date(2027, 1, 5)), [])
        self.assertEqual(reajustes_devidos(date(2027, 6, 5)), [(importada, 2027)])

    def test_buscar_variacoes_converte_e_monta_url(self):
        dados = [{"data": f"01/{m:02d}/2026", "valor": "0.45"} for m in range(3, 13)]
        dados += [{"data": "01/01/2027", "valor": "-0.1"}, {"data": "01/02/2027", "valor": "1.2"}]
        dados.reverse()
        resposta = io.BytesIO(json.dumps(dados).encode())
        with mock.patch("locacao.indices.urlopen", return_value=resposta) as urlopen:
            v = buscar_variacoes("IGPM", date(2026, 3, 1), date(2027, 2, 1))
        url = urlopen.call_args.args[0]
        self.assertIn("bcdata.sgs.189/dados?formato=json&dataInicial=01/03/2026&dataFinal=01/02/2027", url)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)
        self.assertEqual(v, [Decimal("0.45")] * 10 + [Decimal("-0.1"), Decimal("1.2")])

    def test_buscar_poucos_meses_levanta(self):
        resposta = io.BytesIO(json.dumps([{"data": "01/03/2026", "valor": "0.45"}]).encode())
        with mock.patch("locacao.indices.urlopen", return_value=resposta), self.assertRaises(IndiceIndisponivel):
            buscar_variacoes("IPCA", date(2026, 3, 1), date(2027, 2, 1))

    def test_buscar_erro_levanta(self):
        with mock.patch("locacao.indices.urlopen", side_effect=URLError("fora do ar")), \
                self.assertRaises(IndiceIndisponivel):
            buscar_variacoes("IGPM", date(2026, 3, 1), date(2027, 2, 1))

    def test_buscar_indice_desconhecido(self):
        with mock.patch("locacao.indices.urlopen") as urlopen, self.assertRaises(ValueError):
            buscar_variacoes("NENHUM", date(2026, 3, 1), date(2027, 2, 1))
        urlopen.assert_not_called()

    def test_buscar_valor_invalido_levanta(self):
        dados = [{"data": f"01/{m:02d}/2026", "valor": "-"} for m in range(1, 13)]
        with mock.patch("locacao.indices.urlopen", return_value=io.BytesIO(json.dumps(dados).encode())), \
                self.assertRaises(IndiceIndisponivel):
            buscar_variacoes("IGPM", date(2026, 1, 1), date(2026, 12, 1))

    def test_buscar_json_invalido_levanta(self):
        with mock.patch("locacao.indices.urlopen", return_value=io.BytesIO(b"<html>")), \
                self.assertRaises(IndiceIndisponivel):
            buscar_variacoes("IGPM", date(2026, 3, 1), date(2027, 2, 1))
