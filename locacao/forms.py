from decimal import Decimal

from django import forms
from django.db import models

from .dinheiro import primeiro_dia
from .models import (
    AnotacaoDivida, Cobranca, Config, Despesa, Empresa, Imovel, Inquilino, Locacao, TarefaManual, ValorAluguel,
)
from .templatetags.br import brl, mes_br


class DecimalBR(forms.DecimalField):
    """Aceita "1.234,56" (ponto de milhar só quando há vírgula decimal) e "1234.56"."""

    def __init__(self, **kw):
        kw.setdefault("widget", forms.TextInput(attrs={"inputmode": "decimal"}))
        super().__init__(localize=True, **kw)

    def to_python(self, value):
        if isinstance(value, str) and "," in value:
            value = value.replace(".", "")
        return super().to_python(value)


DATA = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})


def campo_br(f, **kw):
    if isinstance(f, models.DecimalField):
        return f.formfield(form_class=DecimalBR, **kw)
    if isinstance(f, models.DateField):
        return f.formfield(widget=DATA, **kw)
    if isinstance(f, models.URLField):
        return f.formfield(assume_scheme="https", **kw)
    return f.formfield(**kw)


class EmpresaForm(forms.ModelForm):
    class Meta:
        model = Empresa
        fields = ["nome", "cnpj", "banco", "acesso_direto"]


IMOVEL_GRUPOS = [
    ("Identificação", ["codigo", "cidade", "endereco", "unidade", "incorporadora", "imobiliaria", "empresa",
                       "participacao_pct"]),
    ("Contas e registros", ["inscricao_iptu", "codigo_agua", "codigo_luz", "registro_cartorio", "proprietario_atual"]),
    ("Outros", ["outros", "observacoes"]),
]


class ImovelForm(forms.ModelForm):
    class Meta:
        model = Imovel
        fields = [n for _, nomes in IMOVEL_GRUPOS for n in nomes]
        formfield_callback = campo_br
        widgets = {"outros": forms.Textarea(attrs={"rows": 3}), "observacoes": forms.Textarea(attrs={"rows": 3})}


class InquilinoForm(forms.ModelForm):
    class Meta:
        model = Inquilino
        fields = ["nome", "documento", "nome2", "documento2", "contato", "observacoes"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}


LOCACAO_GRUPOS = [
    ("Partes", ["imovel", "inquilino", "corretor"]),
    ("Prazo e vencimento", ["inicio", "valor_inicial", "duracao_meses", "termino", "dia_vencimento"]),
    ("Reajuste e condições", ["indice", "mes_base_reajuste", "isencao_meses", "desconto_pontualidade",
                              "dia_limite_desconto", "garantia"]),
    ("Contrato e observações", ["link_contrato", "observacoes"]),
]


class LocacaoForm(forms.ModelForm):
    class Meta:
        model = Locacao
        fields = [
            "imovel", "inquilino", "corretor", "inicio", "duracao_meses", "termino", "dia_vencimento", "indice",
            "mes_base_reajuste", "garantia", "link_contrato", "isencao_meses", "desconto_pontualidade",
            "dia_limite_desconto", "observacoes",
        ]
        formfield_callback = campo_br
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}

    def clean_imovel(self):
        imovel = self.cleaned_data["imovel"]
        outras = imovel.locacoes.filter(ativa=True).exclude(pk=self.instance.pk)
        if (self.instance.pk is None or self.instance.ativa) and outras.exists():
            raise forms.ValidationError("Este imóvel já tem uma locação ativa. Encerre-a antes de cadastrar outra.")
        return imovel


class LocacaoNovaForm(LocacaoForm):
    valor_inicial = DecimalBR(label="Valor inicial do aluguel", max_digits=12, decimal_places=2, min_value=0)


class ValorForm(forms.ModelForm):
    class Meta:
        model = ValorAluguel
        fields = ["vigente_desde", "valor", "motivo"]
        formfield_callback = campo_br


class EncerrarForm(forms.Form):
    data_saida = forms.DateField(label="Data de saída", widget=DATA)


class DespesaForm(forms.ModelForm):
    class Meta:
        model = Despesa
        fields = ["imovel", "tipo", "descricao", "competencia", "vencimento", "valor", "informado_em"]
        formfield_callback = campo_br
        help_texts = {"competencia": "Qualquer dia do mês; vale o mês."}

    def clean_competencia(self):
        return primeiro_dia(self.cleaned_data["competencia"])


class TarefaForm(forms.ModelForm):
    class Meta:
        model = TarefaManual
        fields = ["titulo", "prazo", "imovel"]
        formfield_callback = campo_br


class ConfigForm(forms.ModelForm):
    class Meta:
        model = Config
        fields = ["percentual_comissao", "inicio_historico", "mes_corte", "aviso_termino_dias", "gerar_a_partir_de"]
        formfield_callback = campo_br

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        if self.instance.gerar_a_partir_de:  # definido pela importação; mudar criaria ou esconderia meses
            campo = self.fields["gerar_a_partir_de"]
            campo.disabled = True
            campo.help_text += " Definido pela importação; não se altera pela tela."

    def clean_mes_corte(self):
        d = self.cleaned_data["mes_corte"]
        return d and primeiro_dia(d)


def _dinheiro(label, **kw):
    return DecimalBR(label=label, max_digits=12, decimal_places=2, **kw)


class PagamentoForm(forms.Form):
    data = forms.DateField(label="Data em que entrou", widget=DATA)
    valor = _dinheiro("Valor", min_value=Decimal("0.01"))
    observacoes = forms.CharField(label="Observações", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, abertas=(), **kw):
        """abertas: [(cobranca, saldo)] — uma linha opcional de alocação manual por cobrança."""
        super().__init__(*args, **kw)
        self.abertas = abertas
        for c, s in abertas:
            mes = mes_br(c.competencia)
            self.fields[f"aloc_{c.pk}"] = _dinheiro(
                f"Valor para {mes}", required=False, min_value=Decimal("0.01"), max_value=s,
                error_messages={"max_value": f"O valor para {mes} não pode passar do saldo de {brl(s)}."},
            )

    def principais(self):
        return [self[n] for n in ("data", "valor", "observacoes")]

    def linhas(self):
        return [(c, s, self[f"aloc_{c.pk}"]) for c, s in self.abertas]

    def alocacoes(self):
        """Alocação manual preenchida, ou None (automático)."""
        itens = [(c, self.cleaned_data[f"aloc_{c.pk}"]) for c, _ in self.abertas if self.cleaned_data[f"aloc_{c.pk}"]]
        return itens or None


class PagarCobrancaForm(forms.Form):
    """Pagamento rápido de uma cobrança (_pagar.html desenha os campos à mão, um formulário por linha)."""
    valor = _dinheiro("Valor recebido", min_value=Decimal("0.01"))
    data = forms.DateField(label="Data do pagamento", widget=DATA)
    saldo = forms.DecimalField(label="Saldo da página", max_digits=12, decimal_places=2, widget=forms.HiddenInput)


class CobrancaForm(forms.ModelForm):
    class Meta:
        model = Cobranca
        fields = ["ajuste", "motivo_ajuste", "quitada", "observacoes"]
        formfield_callback = campo_br
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"quitada": "Aceita o recebido como pagamento total; a diferença vira desconto concedido."}


class AnotacaoForm(forms.ModelForm):
    class Meta:
        model = AnotacaoDivida
        fields = ["data", "texto"]
        formfield_callback = campo_br
        widgets = {"texto": forms.Textarea(attrs={"rows": 2})}


class SituacaoDividaForm(forms.ModelForm):
    class Meta:
        model = Locacao
        fields = ["situacao_divida"]


class CalcularReajusteForm(forms.Form):
    ano = forms.IntegerField(min_value=2000, max_value=2100)
    percentual = DecimalBR(label="Percentual acumulado (%)", max_digits=8, decimal_places=4, required=False)


class AprovarReajusteForm(forms.Form):
    valor_aprovado = _dinheiro("Valor aprovado", min_value=Decimal("0.01"))


class ComissaoRecebidaForm(forms.Form):
    recebida_em = forms.DateField(label="Recebida em", widget=DATA)


class ComissoesAteForm(ComissaoRecebidaForm):
    ate = forms.DateField(label="Até o mês", input_formats=["%Y-%m"],
                          widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}))
