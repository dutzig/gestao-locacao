# Gestão de Locação

Sistema web para acompanhar locações de imóveis: cobranças mensais, pagamentos,
comissão, despesas, tarefas e visão anual. Dois perfis: administrador
(`is_staff=True`, acesso total) e visualização (`is_staff=False`, só dashboard e
visão anual). Django 5 + SQLite, interface em português.

Este repositório contém apenas código. Dados reais, planilhas, bancos, backups
e `.env` ficam fora do git.

## Rodar localmente

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
export DJANGO_DEBUG=1
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

## Testes

```bash
.venv/bin/python manage.py test
```

## Publicar

Veja [docs/DEPLOY.md](docs/DEPLOY.md).
