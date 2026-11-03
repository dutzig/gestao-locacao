from django.core.management.base import BaseCommand, CommandError

from locacao.importacao import importar

ROTULOS = {
    "empresas": "Empresas", "imoveis": "Imóveis", "inquilinos": "Inquilinos", "locacoes": "Locações",
    "cobrancas": "Cobranças", "pagamentos": "Pagamentos", "revisao": "Itens para revisão",
}


class Command(BaseCommand):
    help = "Importa a planilha .xlsx. APAGA todos os dados do sistema (exceto configuração e usuários) e reimporta."

    def add_arguments(self, parser):
        parser.add_argument("caminho")
        parser.add_argument("--forcar", action="store_true", help="Apaga mesmo havendo dados lançados no sistema (pagamentos, despesas, tarefas, reajustes etc.).")

    def handle(self, caminho, forcar, **_):
        try:
            contagens = importar(caminho, forcar=forcar)
        except RuntimeError as e:
            raise CommandError(str(e))
        self.stdout.write("Importação concluída.")
        for chave, rotulo in ROTULOS.items():
            self.stdout.write(f"{rotulo}: {contagens[chave]}")
