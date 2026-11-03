"""Importação da planilha (.xlsx exportado do Google Sheets). Histórico entra como está."""
import re
import unicodedata
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .cobrancas import vencimento_de
from .dinheiro import centavos, primeiro_dia, somar_meses
from .models import (
    Alocacao, AnotacaoDivida, Cobranca, ComissaoMes, Config, Despesa, Empresa, Imovel, Inquilino, Locacao,
    Pagamento, Reajuste, RevisaoImportacao, TarefaManual, ValorAluguel,
)

MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
INDICES = {"igp-m": "IGPM", "igpm": "IGPM", "ipca": "IPCA"}
LIMITE = 200  # max_length dos campos de texto curtos
# Ordem respeita os PROTECT.
TABELAS = (Alocacao, Pagamento, Cobranca, ValorAluguel, Reajuste, AnotacaoDivida, Locacao, Inquilino, Despesa,
           TarefaManual, Imovel, Empresa, ComissaoMes, RevisaoImportacao)
OBRIGATORIOS = {
    "imoveis": ["id imovel", "cidade", "endereco", "unidade", "inscricao", "incorporadora", "imobiliaria",
                "observacoes", "iptu", "energia"],
    "contratos": ["id imovel", "inquilino", "valor aluguel", "vencimento", "banco pagamento", "data inicio",
                  "duracao", "data termino", "indice", "data proximo reajuste", "link contrato pdf", "observacoes"],
    "inquilinos": ["id imovel", "inquilino (resumo)", "nome locatario 1 / razao social", "cpf/cnpj locatario 1",
                   "nome locatario 2 / representante", "cpf/cnpj locatario 2", "tipo de garantia", "observacoes",
                   "contato"],
    "lancamentos": ["data contrato", "id imovel", "inquilino", "mes/ano referencia", "valor aluguel",
                    "valor recebido", "data pagamento", "comissao (5%)", "status", "observacoes"],
}


def _norm(v) -> str:
    s = unicodedata.normalize("NFKD", str(v or ""))
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).lower().split())


def _vazio(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (datetime, date)):
        return f"{v:%d/%m/%Y}"
    return str(v).strip()


def _texto_br(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,}".replace(",", "_").replace(".", ",").replace("_", ".")
    return _texto(v)


def _dinheiro(v) -> Decimal | None:
    """Vazio -> None; número ou texto pt-BR ("R$ 1.000,00", "900,00") -> Decimal; resto -> ValueError."""
    if _vazio(v):
        return None
    if isinstance(v, bool):
        raise ValueError(v)
    s = str(v).strip()
    if isinstance(v, str):
        s = s.replace("R$", "").replace(" ", "")
        if "," in s or re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "").replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(v) from None
    if not d.is_finite():
        raise ValueError(v)
    return centavos(d)


def _data(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def _competencia(v) -> date | None:
    if d := _data(v):
        return primeiro_dia(d)
    m = re.fullmatch(r"([a-z]{3})\.?/(\d{4})", _norm(v))
    if m and m[1] in MESES:
        return date(int(m[2]), MESES.index(m[1]) + 1, 1)
    return None


def _inteiro(v) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not float(v).is_integer():
        return None
    return int(v)


class Linha:
    def __init__(self, aba, n, cab, chaves, valores):
        self.aba, self.n, self.cab, self.chaves, self.valores = aba, n, cab, chaves, valores
        self.d = {k: v for k, v in zip(chaves, valores) if k}

    def __getitem__(self, chave):
        return self.d.get(chave)

    def texto(self, chave) -> str:
        return _texto(self.d.get(chave))

    def dados(self) -> str:
        return "; ".join(f"{_texto(h) or '(sem título)'}: {_texto(v)}"
                         for h, v in zip(self.cab, self.valores) if not _vazio(v))


def _revisao(linha: Linha, motivo: str):
    RevisaoImportacao.objects.create(aba=linha.aba, linha=linha.n, motivo=motivo, dados=linha.dados())


def _abas(wb) -> dict[str, list[Linha]]:
    """Linhas não vazias de cada aba obrigatória; RuntimeError se faltar aba ou cabeçalho."""
    faltas, abas = [], {}
    for nome, obrigatorios in OBRIGATORIOS.items():
        ws = next((ws for ws in wb.worksheets if _norm(ws.title) == nome), None)
        if ws is None:
            faltas.append(f"aba {nome}")
            continue
        rows = ws.iter_rows(values_only=True)
        cab = list(next(rows, ()))
        chaves = [_norm(c) if c is not None else None for c in cab]
        faltas += [f"coluna '{c}' na aba {ws.title}" for c in obrigatorios if c not in chaves]
        abas[nome] = [Linha(ws.title, n, cab, chaves, valores)
                      for n, valores in enumerate(rows, start=2) if not all(_vazio(v) for v in valores)]
    if faltas:
        raise RuntimeError("Planilha fora do formato esperado; falta: " + ", ".join(faltas) + ".")
    return abas


def _curto(linha, texto, rotulo, extras, limite=LIMITE) -> str:
    """Texto que não cabe no campo: guarda inteiro em `extras` (outros/observações) e manda para revisão."""
    if len(texto) <= limite:
        return texto
    extras.append(f"{rotulo}: {texto}")
    _revisao(linha, f"{rotulo} com mais de {limite} caracteres (texto completo nas observações/outros)")
    return texto[:limite]


class _Importacao:
    def __init__(self, abas):
        self.abas = abas
        self.imoveis: dict[str, Imovel] = {}
        # (código do imóvel, nome normalizado) -> (locação, valor do contrato)
        self.locacoes: dict[tuple[str, str], tuple[Locacao, Decimal | None]] = {}
        self.rejeitados: set[tuple[str, str]] = set()  # contratos com valor inválido

    def importar_imoveis(self):
        sem_empresa = []
        for linha in self.abas["imoveis"]:
            codigo = linha.texto("id imovel")
            if not codigo:
                _revisao(linha, "imóvel sem ID")
                continue
            j = linha.chaves.index("iptu")
            iptu = [linha.valores[j]]
            j += 1
            while j < len(linha.chaves) and linha.chaves[j] is None:
                iptu.append(linha.valores[j])
                j += 1
            iptu = [_texto_br(v) for v in iptu if not _vazio(v)]
            outros = [f"IPTU: {' · '.join(iptu)}"] if iptu else []
            campos = {
                campo: _curto(linha, linha.texto(chave), rotulo, outros)
                for campo, chave, rotulo in [
                    ("codigo", "id imovel", "ID Imovel"), ("cidade", "cidade", "Cidade"),
                    ("endereco", "endereco", "Endereço"), ("unidade", "unidade", "Unidade"),
                    ("inscricao_iptu", "inscricao", "Inscrição"), ("incorporadora", "incorporadora", "Incorporadora"),
                    ("imobiliaria", "imobiliaria", "Imobiliária"), ("codigo_luz", "energia", "Energia"),
                ]
            }
            incorporadora = campos["incorporadora"]
            imovel = Imovel.objects.create(
                **campos, observacoes=linha.texto("observacoes"), outros="\n".join(outros), importado=True,
                empresa=Empresa.objects.get_or_create(nome=incorporadora, defaults={"importado": True})[0]
                if incorporadora else None,
            )
            self.imoveis[codigo] = imovel
            if not incorporadora:
                sem_empresa.append((imovel, linha))
        for imovel, linha in sem_empresa:  # depois, para contar todos os imóveis com incorporadora
            imovel.empresa = (Empresa.objects.filter(imovel__cidade=imovel.cidade)
                              .annotate(qtd=Count("imovel")).order_by("-qtd", "pk").first())
            if imovel.empresa:
                imovel.save(update_fields=["empresa"])
                _revisao(linha, f"empresa atribuída pela cidade ({imovel.empresa.nome})")
            else:
                _revisao(linha, "imóvel sem incorporadora e sem empresa na cidade")

    def importar_contratos(self):
        for linha in self.abas["contratos"]:
            codigo, nome = linha.texto("id imovel"), linha.texto("inquilino")
            if not codigo:
                if _norm(nome) != "total":
                    _revisao(linha, "contrato sem imóvel")
                continue
            imovel = self.imoveis.get(codigo)
            if imovel is None:
                _revisao(linha, "contrato de imóvel inexistente na aba Imoveis")
                continue
            if (codigo, _norm(nome)) in self.locacoes or (codigo, _norm(nome)) in self.rejeitados:
                _revisao(linha, "contrato repetido para o mesmo imóvel e inquilino (não importado)")
                continue
            try:
                valor = _dinheiro(linha["valor aluguel"])
            except ValueError:
                _revisao(linha, "contrato com valor de aluguel inválido (não importado)")
                self.rejeitados.add((codigo, _norm(nome)))
                continue
            if valor is None:
                _revisao(linha, "contrato sem valor de aluguel")
            inicio = _data(linha["data inicio"])
            if inicio is None:
                inicio = primeiro_dia(timezone.localdate())
                _revisao(linha, "contrato sem data de início (usado o mês atual)")
            dia = _inteiro(linha["vencimento"])
            if dia is None or not 1 <= dia <= 31:
                dia = inicio.day
                _revisao(linha, "contrato sem dia de vencimento válido (usado o dia do início)")
            obs = []
            banco, empresa = linha.texto("banco pagamento"), imovel.empresa
            if banco and not (empresa and empresa.banco == banco):
                if re.fullmatch(r"\w+", banco) and len(banco) <= LIMITE and empresa and not empresa.banco:
                    empresa.banco = banco
                    empresa.save(update_fields=["banco"])
                else:
                    obs.append(f"Banco: {banco}")
            observacao = linha.texto("observacoes")
            if observacao == "0.8":
                imovel.participacao_pct = Decimal("80")
                imovel.save(update_fields=["participacao_pct"])
            elif observacao:
                obs.append(observacao)
            link = linha.texto("link contrato pdf")
            if link:
                try:
                    URLValidator(schemes=["http", "https"])(link)
                    if len(link) > 500:
                        raise ValidationError("longo")
                except ValidationError:
                    obs.append(f"Link do contrato: {link}")
                    _revisao(linha, "link do contrato inválido (guardado nas observações)")
                    link = ""
            duracao, reajuste = _inteiro(linha["duracao"]), _data(linha["data proximo reajuste"])
            inquilino = Inquilino.objects.create(nome=_curto(linha, nome, "Inquilino", obs), importado=True)
            loc = Locacao.objects.create(
                imovel=imovel, inquilino=inquilino, inicio=inicio, dia_vencimento=dia, duracao_meses=duracao,
                termino=_data(linha["data termino"]), indice=INDICES.get(_norm(linha["indice"]), "NENHUM"),
                mes_base_reajuste=reajuste.month if reajuste else None, link_contrato=link,
                observacoes="\n".join(obs), importada=True,
            )
            if valor is not None:
                ValorAluguel.objects.create(locacao=loc, vigente_desde=inicio, valor=valor, motivo="INICIAL")
            self.locacoes[(codigo, _norm(nome))] = (loc, valor)

    def _ler_lancamentos(self) -> list[dict]:
        validas = []
        for linha in self.abas["lancamentos"]:
            if all(_vazio(v) for k, v in linha.d.items() if k != "status"):
                continue  # linha só com status
            codigo, nome = linha.texto("id imovel"), linha.texto("inquilino")
            comp = _competencia(linha["mes/ano referencia"])
            try:
                valores = [_dinheiro(linha[k]) for k in ("valor aluguel", "valor recebido", "comissao (5%)")]
            except ValueError:
                valores = None
            motivo = ("lançamento sem imóvel" if not codigo else
                      "lançamento sem referência (mês/ano) válida" if comp is None else
                      "lançamento de imóvel inexistente na aba Imoveis" if codigo not in self.imoveis else
                      "lançamento sem inquilino" if not nome else
                      "lançamento de contrato não importado" if (codigo, _norm(nome)) in self.rejeitados else
                      "lançamento com valor inválido (não importado)" if valores is None else None)
            if motivo:
                _revisao(linha, motivo)
                continue
            esperado, recebido, comissao = valores
            validas.append({"linha": linha, "chave": (codigo, _norm(nome)), "nome": nome, "comp": comp,
                            "esperado": esperado, "recebido": recebido or Decimal("0"), "comissao": comissao})
        return validas

    def _criar_antigas(self, lancamentos):
        """Nomes sem contrato = inquilinos antigos: uma locação encerrada por (imóvel, nome)."""
        antigas: dict[tuple[str, str], list[dict]] = {}
        for lanc in sorted(lancamentos, key=lambda x: x["comp"]):
            if lanc["chave"] not in self.locacoes:
                antigas.setdefault(lanc["chave"], []).append(lanc)
        for chave, grupo in antigas.items():
            primeira = grupo[0]
            venc = _data(primeira["linha"]["data contrato"])
            if venc is None:
                _revisao(primeira["linha"], "inquilino antigo sem data de vencimento (usado dia 1)")
            _revisao(primeira["linha"], "inquilino sem contrato — conferir se é o mesmo do contrato")
            obs = []
            inquilino = Inquilino.objects.create(nome=_curto(primeira["linha"], primeira["nome"], "Inquilino", obs),
                                                 importado=True)
            loc = Locacao.objects.create(
                imovel=self.imoveis[chave[0]], inquilino=inquilino, inicio=primeira["comp"],
                dia_vencimento=venc.day if venc else 1, indice="NENHUM", ativa=False,
                data_saida=somar_meses(grupo[-1]["comp"], 1), observacoes="\n".join(obs), importada=True,
            )
            valor = next((g["esperado"] for g in grupo if g["esperado"] is not None), None)
            if valor is not None:
                ValorAluguel.objects.create(locacao=loc, vigente_desde=loc.inicio, valor=valor, motivo="INICIAL")
            self.locacoes[chave] = (loc, None)  # sem contrato: valor só da própria linha

    def importar_lancamentos(self) -> list[date]:
        lancamentos = self._ler_lancamentos()
        self._criar_antigas(lancamentos)
        competencias, vistas = [], set()
        for lanc in lancamentos:
            linha, comp, recebido = lanc["linha"], lanc["comp"], lanc["recebido"]
            loc, valor_contrato = self.locacoes[lanc["chave"]]
            if (loc.pk, comp) in vistas:
                _revisao(linha, "lançamento duplicado do mesmo mês (não importado)")
                continue
            vistas.add((loc.pk, comp))
            esperado = lanc["esperado"] if lanc["esperado"] is not None else valor_contrato
            if esperado is None:
                if recebido <= 0:
                    _revisao(linha, "lançamento sem valor de aluguel nem no contrato (não importado)")
                    continue
                _revisao(linha, "lançamento sem valor de aluguel nem no contrato (esperado = recebido)")
                esperado = recebido
            competencias.append(comp)
            cobranca = Cobranca.objects.create(
                locacao=loc, competencia=comp, valor_esperado=esperado,
                vencimento=_data(linha["data contrato"]) or vencimento_de(loc, comp),
                quitada=_norm(linha["status"]) == "recebido", comissao_importada=lanc["comissao"],
                observacoes=linha.texto("observacoes"),
            )
            if recebido > 0:
                pagamento = Pagamento.objects.create(
                    locacao=loc, data=_data(linha["data pagamento"]), valor=recebido, importado=True,
                )
                Alocacao.objects.create(pagamento=pagamento, cobranca=cobranca, valor=recebido)
        return competencias

    def importar_inquilinos(self):
        vistas = set()
        for linha in self.abas["inquilinos"]:
            achado = self.locacoes.get((linha.texto("id imovel"), _norm(linha["inquilino (resumo)"])))
            if achado is None:
                _revisao(linha, "inquilino sem contrato nem lançamento correspondente")
                continue
            loc = achado[0]
            if loc.pk in vistas:
                _revisao(linha, "inquilino repetido para a mesma locação (não importado)")
                continue
            vistas.add(loc.pk)
            inq = loc.inquilino
            obs = [inq.observacoes] if inq.observacoes else []
            if texto := linha.texto("observacoes"):
                obs.append(texto)
            inq.nome = _curto(linha, linha.texto("nome locatario 1 / razao social"), "Nome", obs) or inq.nome
            inq.documento = _curto(linha, linha.texto("cpf/cnpj locatario 1"), "CPF/CNPJ", obs)
            inq.nome2 = _curto(linha, linha.texto("nome locatario 2 / representante"), "Locatário 2", obs)
            inq.documento2 = _curto(linha, linha.texto("cpf/cnpj locatario 2"), "CPF/CNPJ 2", obs)
            inq.contato = _curto(linha, linha.texto("contato"), "Contato", obs)
            inq.observacoes = "\n".join(obs)
            inq.save()
            loc_obs = [loc.observacoes] if loc.observacoes else []
            loc.garantia = _curto(linha, linha.texto("tipo de garantia"), "Garantia", loc_obs)
            loc.observacoes = "\n".join(loc_obs)
            loc.save(update_fields=["garantia", "observacoes"])


def _ha_dados_do_usuario() -> bool:
    return (Pagamento.objects.filter(importado=False).exists()
            or ValorAluguel.objects.exclude(motivo="INICIAL").exists()
            or any(m.objects.exists() for m in (Despesa, TarefaManual, ComissaoMes, Reajuste, AnotacaoDivida))
            or RevisaoImportacao.objects.filter(resolvida=True).exists()
            or Cobranca.objects.exclude(ajuste=0).exists()
            or Empresa.objects.filter(acesso_direto=True).exists()
            or Locacao.objects.filter(Q(importada=False) | Q(editada=True) | ~Q(situacao_divida="NENHUMA")
                                      | Q(desconto_pontualidade__isnull=False)).exists()
            or any(m.objects.filter(importado=False).exists() for m in (Imovel, Inquilino, Empresa)))


def importar(caminho: str, forcar: bool = False) -> dict[str, int]:
    try:
        wb = load_workbook(caminho, data_only=True)
    except (OSError, zipfile.BadZipFile, InvalidFileException, KeyError) as e:
        raise RuntimeError(f"Não foi possível abrir a planilha {caminho}: {e}") from e
    with transaction.atomic():
        if _ha_dados_do_usuario() and not forcar:
            raise RuntimeError(
                "Já existem dados lançados ou editados no sistema (pagamentos, despesas, tarefas, reajustes, "
                "anotações, comissões, ajustes, revisões resolvidas, acesso das empresas ou cadastros feitos à mão); "
                "use --forcar para apagar tudo e reimportar."
            )
        abas = _abas(wb)
        for modelo in TABELAS:
            modelo.objects.all().delete()
        imp = _Importacao(abas)
        imp.importar_imoveis()
        imp.importar_contratos()
        competencias = imp.importar_lancamentos()
        imp.importar_inquilinos()
        RevisaoImportacao.objects.create(
            aba="Outros", linha=1, motivo="Conferir aba Outros manualmente (códigos de água, inscrições, IPTU)",
        )
        if competencias:
            config = Config.get()
            config.inicio_historico = min(competencias)
            # Nunca depois do mês da importação: um inquilino adiantado não tira o mês atual dos outros.
            config.gerar_a_partir_de = min(somar_meses(max(competencias), 1), primeiro_dia(timezone.localdate()))
            config.save(update_fields=["inicio_historico", "gerar_a_partir_de"])
        return {
            "empresas": Empresa.objects.count(), "imoveis": Imovel.objects.count(),
            "inquilinos": Inquilino.objects.count(), "locacoes": Locacao.objects.count(),
            "cobrancas": Cobranca.objects.count(), "pagamentos": Pagamento.objects.count(),
            "revisao": RevisaoImportacao.objects.count(),
        }
