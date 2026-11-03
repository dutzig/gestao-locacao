from datetime import date
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum


def Dinheiro(verbose_name, **kw):
    return models.DecimalField(verbose_name, max_digits=12, decimal_places=2, **kw)


def Texto(verbose_name):
    return models.CharField(verbose_name, max_length=200, blank=True)


class Config(models.Model):
    percentual_comissao = models.DecimalField("percentual da comissão (%)", max_digits=5, decimal_places=2, default=Decimal("5.00"))
    inicio_historico = models.DateField("início do histórico", default=date(2026, 2, 1))
    mes_corte = models.DateField("mês de corte", null=True, blank=True, help_text="Dia 1 do mês; vazio = regra nova desligada.")
    aviso_termino_dias = models.IntegerField("aviso de término (dias)", default=90)
    gerar_a_partir_de = models.DateField(
        "gerar cobranças automaticamente a partir de", null=True, blank=True,
        help_text="Meses anteriores vêm da planilha importada; nas locações que vieram dela, o sistema não cria "
                  "cobranças antes desta data. Locações cadastradas depois geram desde o próprio início.",
    )

    class Meta:
        verbose_name = "configuração"
        verbose_name_plural = "configuração"

    def __str__(self):
        return "Configuração"

    @classmethod
    def get(cls):
        return cls.objects.get_or_create(pk=1)[0]


class Empresa(models.Model):
    nome = models.CharField("nome", max_length=200)
    cnpj = Texto("CNPJ")
    banco = Texto("banco")
    acesso_direto = models.BooleanField("acesso direto à conta", default=False)
    importado = models.BooleanField("veio da importação (sem edição depois)", default=False)

    class Meta:
        verbose_name = "empresa"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Imovel(models.Model):
    codigo = models.CharField("código", max_length=200, unique=True)
    cidade = Texto("cidade")
    endereco = Texto("endereço")
    unidade = Texto("unidade")
    incorporadora = Texto("incorporadora")
    imobiliaria = Texto("imobiliária/corretor de origem")
    empresa = models.ForeignKey(Empresa, models.PROTECT, null=True, blank=True, verbose_name="empresa")
    participacao_pct = models.DecimalField("participação do proprietário (%)", max_digits=5, decimal_places=2, default=Decimal("100"))
    inscricao_iptu = Texto("inscrição do IPTU")
    codigo_agua = Texto("código da água")
    codigo_luz = Texto("código da luz")
    registro_cartorio = Texto("registro no cartório")
    proprietario_atual = Texto("proprietário atual")
    outros = models.TextField("outros", blank=True)
    observacoes = models.TextField("observações", blank=True)
    importado = models.BooleanField("veio da importação (sem edição depois)", default=False)

    class Meta:
        verbose_name = "imóvel"
        verbose_name_plural = "imóveis"
        ordering = ["codigo"]

    def __str__(self):
        return self.codigo

    @property
    def locacao_ativa(self):
        return self.locacoes.filter(ativa=True).order_by("-inicio").first()

    @property
    def vago(self):
        return self.locacao_ativa is None


class Inquilino(models.Model):
    nome = models.CharField("locatário (nome/razão social)", max_length=200)
    documento = Texto("CPF/CNPJ")
    nome2 = Texto("locatário 2 / representante")
    documento2 = Texto("CPF/CNPJ do locatário 2")
    contato = Texto("contato")
    observacoes = models.TextField("observações", blank=True)
    importado = models.BooleanField("veio da importação (sem edição depois)", default=False)

    class Meta:
        verbose_name = "inquilino"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Locacao(models.Model):
    INDICES = [("IGPM", "IGP-M"), ("IPCA", "IPCA"), ("NENHUM", "Nenhum")]
    SITUACOES_DIVIDA = [
        ("NENHUMA", "Nenhuma"), ("AMIGAVEL", "Amigável"), ("JURIDICO", "Jurídico"),
        ("ACORDO", "Acordo"), ("QUITADA", "Quitada"), ("BAIXADA", "Baixada"),
    ]

    imovel = models.ForeignKey(Imovel, models.PROTECT, related_name="locacoes", verbose_name="imóvel")
    inquilino = models.ForeignKey(Inquilino, models.PROTECT, verbose_name="inquilino")
    corretor = Texto("corretor")
    inicio = models.DateField("início")
    duracao_meses = models.IntegerField("duração (meses)", null=True, blank=True)
    termino = models.DateField("término", null=True, blank=True)
    dia_vencimento = models.IntegerField("dia de vencimento", validators=[MinValueValidator(1), MaxValueValidator(31)])
    indice = models.CharField("índice de reajuste", max_length=10, choices=INDICES)
    mes_base_reajuste = models.IntegerField(
        "mês-base do reajuste", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    garantia = Texto("garantia")
    link_contrato = models.URLField("link do contrato", max_length=500, blank=True)
    isencao_meses = models.IntegerField("meses de isenção", default=0)
    desconto_pontualidade = Dinheiro("desconto por pontualidade", null=True, blank=True)
    dia_limite_desconto = models.IntegerField(
        "dia-limite do desconto", null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(31)]
    )
    observacoes = models.TextField("observações", blank=True)
    ativa = models.BooleanField("ativa", default=True)
    data_saida = models.DateField("data de saída", null=True, blank=True)
    situacao_divida = models.CharField("situação da dívida", max_length=10, choices=SITUACOES_DIVIDA, default="NENHUMA")
    importada = models.BooleanField("veio da importação", default=False)
    editada = models.BooleanField("editada à mão (ela ou suas cobranças/valores)", default=False)

    class Meta:
        verbose_name = "locação"
        verbose_name_plural = "locações"
        ordering = ["-inicio"]
        constraints = [
            models.CheckConstraint(condition=Q(dia_vencimento__range=(1, 31)), name="dia_vencimento_1_31"),
            models.CheckConstraint(
                condition=Q(dia_limite_desconto__isnull=True) | Q(dia_limite_desconto__range=(1, 31)),
                name="dia_limite_desconto_1_31",
            ),
            models.CheckConstraint(
                condition=Q(mes_base_reajuste__isnull=True) | Q(mes_base_reajuste__range=(1, 12)),
                name="mes_base_reajuste_1_12",
            ),
        ]

    def __str__(self):
        return f"{self.imovel} — {self.inquilino}"

    def valor_em(self, d):
        v = self.valores.filter(vigente_desde__lte=d).order_by("-vigente_desde").first()
        return v.valor if v else None


class ValorAluguel(models.Model):
    MOTIVOS = [("INICIAL", "Inicial"), ("REAJUSTE", "Reajuste"), ("ESCALONAMENTO", "Escalonamento"), ("ACORDO", "Acordo")]

    locacao = models.ForeignKey(Locacao, models.CASCADE, related_name="valores", verbose_name="locação")
    vigente_desde = models.DateField("vigente desde")
    valor = Dinheiro("valor")
    motivo = models.CharField("motivo", max_length=15, choices=MOTIVOS)

    class Meta:
        verbose_name = "valor do aluguel"
        verbose_name_plural = "valores do aluguel"
        ordering = ["vigente_desde"]
        constraints = [models.UniqueConstraint(fields=["locacao", "vigente_desde"], name="valor_unico_por_data")]


class Cobranca(models.Model):
    locacao = models.ForeignKey(Locacao, models.PROTECT, related_name="cobrancas", verbose_name="locação")
    competencia = models.DateField("competência")
    vencimento = models.DateField("vencimento")
    valor_esperado = Dinheiro("valor esperado")
    ajuste = Dinheiro("ajuste", default=Decimal("0"), help_text="Negativo = desconto.")
    motivo_ajuste = Texto("motivo do ajuste")
    quitada = models.BooleanField("quitada", default=False)
    comissao_importada = Dinheiro("comissão importada", null=True, blank=True)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "cobrança"
        ordering = ["competencia"]
        constraints = [models.UniqueConstraint(fields=["locacao", "competencia"], name="cobranca_unica_por_mes")]

    def __str__(self):
        return f"{self.locacao} — {self.competencia:%m/%Y}"

    @property
    def recebido(self):
        return sum((a.valor for a in self.alocacoes.all()), Decimal("0"))  # usa prefetch se houver


class Pagamento(models.Model):
    locacao = models.ForeignKey(Locacao, models.PROTECT, related_name="pagamentos", verbose_name="locação")
    data = models.DateField("data em que entrou", null=True, blank=True)
    valor = Dinheiro("valor")
    observacoes = models.TextField("observações", blank=True)
    importado = models.BooleanField("importado", default=False)

    class Meta:
        verbose_name = "pagamento"
        ordering = ["data"]

    @property
    def nao_alocado(self):
        return self.valor - (self.alocacoes.aggregate(t=Sum("valor"))["t"] or Decimal("0"))


class Alocacao(models.Model):
    pagamento = models.ForeignKey(Pagamento, models.CASCADE, related_name="alocacoes", verbose_name="pagamento")
    cobranca = models.ForeignKey(Cobranca, models.PROTECT, related_name="alocacoes", verbose_name="cobrança")
    valor = Dinheiro("valor")

    class Meta:
        verbose_name = "alocação"
        verbose_name_plural = "alocações"


class AnotacaoDivida(models.Model):
    locacao = models.ForeignKey(Locacao, models.CASCADE, related_name="anotacoes", verbose_name="locação")
    data = models.DateField("data")
    texto = models.TextField("anotação")

    class Meta:
        verbose_name = "anotação da dívida"
        verbose_name_plural = "anotações da dívida"
        ordering = ["data"]


class Reajuste(models.Model):
    DECISOES = [("PENDENTE", "Pendente"), ("APROVADO", "Aprovado"), ("RECUSADO", "Recusado")]

    locacao = models.ForeignKey(Locacao, models.CASCADE, related_name="reajustes", verbose_name="locação")
    ano = models.IntegerField("ano")
    indice = models.CharField("índice", max_length=10, choices=Locacao.INDICES)
    percentual = models.DecimalField("percentual acumulado (%)", max_digits=8, decimal_places=4, null=True, blank=True)
    valor_atual = Dinheiro("valor atual")
    valor_calculado = Dinheiro("valor calculado", null=True, blank=True)
    enviado_em = models.DateField("enviado ao proprietário em", null=True, blank=True)
    decisao = models.CharField("decisão", max_length=10, choices=DECISOES, default="PENDENTE")
    valor_aprovado = Dinheiro("valor aprovado", null=True, blank=True)
    decidido_em = models.DateField("decidido em", null=True, blank=True)
    vigente_desde = models.DateField("vigente desde")
    aplicado_em = models.DateField("aplicado em", null=True, blank=True)

    class Meta:
        verbose_name = "reajuste"
        ordering = ["-ano"]
        constraints = [models.UniqueConstraint(fields=["locacao", "ano"], name="reajuste_unico_por_ano")]


class Despesa(models.Model):
    TIPOS = [("IPTU", "IPTU"), ("AGUA", "Água"), ("CONDOMINIO", "Condomínio"), ("OUTRA", "Outra")]

    imovel = models.ForeignKey(Imovel, models.CASCADE, related_name="despesas", verbose_name="imóvel")
    tipo = models.CharField("tipo", max_length=10, choices=TIPOS)
    descricao = Texto("descrição")
    competencia = models.DateField("competência")
    vencimento = models.DateField("vencimento", null=True, blank=True)
    valor = Dinheiro("valor")
    informado_em = models.DateField("informado ao proprietário em", null=True, blank=True)

    class Meta:
        verbose_name = "despesa"
        ordering = ["competencia"]


class TarefaManual(models.Model):
    titulo = models.CharField("título", max_length=200)
    prazo = models.DateField("prazo", null=True, blank=True)
    imovel = models.ForeignKey(Imovel, models.SET_NULL, null=True, blank=True, verbose_name="imóvel")
    concluida = models.BooleanField("concluída", default=False)
    criada_em = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        verbose_name = "tarefa manual"
        verbose_name_plural = "tarefas manuais"
        ordering = ["concluida", "prazo"]

    def __str__(self):
        return self.titulo


class ComissaoMes(models.Model):
    mes = models.DateField("mês", unique=True)
    recebida_em = models.DateField("recebida em", null=True, blank=True)
    valor_recebido = Dinheiro("valor recebido", null=True, blank=True)  # comissão apurada quando foi marcada
    observacao = Texto("observação")

    class Meta:
        verbose_name = "comissão do mês"
        verbose_name_plural = "comissões por mês"
        ordering = ["mes"]


class RevisaoImportacao(models.Model):
    aba = models.CharField("aba", max_length=200)
    linha = models.IntegerField("linha")
    motivo = models.CharField("motivo", max_length=200)
    dados = models.TextField("dados", blank=True)
    resolvida = models.BooleanField("resolvida", default=False)

    class Meta:
        verbose_name = "revisão da importação"
        verbose_name_plural = "revisões da importação"
        ordering = ["aba", "linha"]
