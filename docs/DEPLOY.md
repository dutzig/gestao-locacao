# Deploy

Container Docker atrás de um Traefik já existente no servidor (provider docker,
`exposedbydefault=false`). Nenhuma porta é publicada no host.

## Requisitos no servidor

- Docker com Compose v2 e o Traefik rodando, com rede externa conhecida.
- `age` para o backup: `apt install age`.

## Passos

```bash
git clone <url-do-repositorio> gestao && cd gestao
cp .env.example .env              # editar: ver abaixo
cp backup.env.example backup.env && chmod 600 backup.env
mkdir dados && sudo chown 1000:1000 dados   # o container roda como uid 1000
docker compose up -d --build
docker compose ps         # aguardar "healthy"
```

No `.env`: `DJANGO_SECRET_KEY` longa e aleatória
(`python3 -c "import secrets; print(secrets.token_urlsafe(50))"`),
`APP_HOST` (hostname público), `TRAEFIK_NETWORK`, `TRAEFIK_ENTRYPOINT`,
`TRAEFIK_CERTRESOLVER` (nomes que o Traefik do servidor usa). As variáveis do
backup ficam à parte, em `backup.env`, e não chegam ao container. Valores com
`$` ou espaços devem ficar entre aspas simples. Nenhum dos dois vai para o git.

Se o `docker compose up` criou `dados/` sozinho (dono root), o app falha com
`unable to open database file` / `readonly database`: corrija com
`sudo chown 1000:1000 dados` e `docker compose restart`.

O `migrate` roda sozinho a cada início do container.

## Usuários

Administrador:

```bash
docker compose exec gestao python manage.py createsuperuser
```

Usuário de visualização (só dashboard e visão anual):

```bash
docker compose exec gestao python manage.py shell -c "from django.contrib.auth import get_user_model as g; g().objects.create_user('visualizacao', is_staff=False)"
docker compose exec gestao python manage.py changepassword visualizacao
```

(o usuário nasce sem senha utilizável; a senha é digitada no `changepassword`,
sem ficar no histórico.)

## Importar a planilha

Copie o `.xlsx` para `dados/` no servidor, importe e apague o arquivo:

```bash
docker compose exec gestao python manage.py importar_planilha /data/planilha.xlsx
rm dados/planilha.xlsx
```

## Backup semanal

O `scripts/backup.sh` faz uma cópia consistente do SQLite, criptografa com
`age` usando só a chave pública e envia ao Telegram. A chave privada nunca fica
no servidor.

Gerar o par de chaves no Mac (uma vez):

```bash
brew install age
age-keygen -o chave.txt     # imprime "Public key: age1..."
```

Guarde `chave.txt` no gerenciador de senhas e apague-a do disco. Coloque só a
chave pública (`age1...`) em `AGE_RECIPIENT` no `backup.env`, junto com
`TG_TOKEN` e `TG_CHAT` do bot.

O banco é copiado dentro do container e segue por pipe direto para o `age`; o
texto claro nunca é gravado no disco do host. Só o arquivo criptografado existe
(em diretório temporário, apagado ao fim).

Testar à mão e agendar no crontab do root (domingos 03:00):

```bash
sudo ./scripts/backup.sh
sudo crontab -e
# 0 3 * * 0 /caminho/para/gestao/scripts/backup.sh >> /var/log/gestao-backup.log 2>&1
```

O script sai com erro (`set -euo pipefail`) se qualquer etapa falhar.

### Verificar backup

Todo domingo deve chegar um `gestao-AAAAMMDD.sqlite3.age` no Telegram. Se não
chegou, veja `/var/log/gestao-backup.log` e rode `sudo ./scripts/backup.sh` à
mão. De tempos em tempos, baixe um e teste a restauração (abaixo).

## Restaurar

Baixe o `.age` do Telegram e, no Mac com a `chave.txt`:

```bash
age -d -i chave.txt gestao-AAAAMMDD.sqlite3.age > db.sqlite3
```

No servidor: `docker compose down`, substitua `dados/db.sqlite3` pelo arquivo
restaurado (`sudo chown 1000:1000`), `docker compose up -d`.

## Avisos esperados do `check --deploy`

Com `DJANGO_DEBUG=0`, o `manage.py check --deploy` aponta (aceitos, pois o
TLS e o redirecionamento 80→443 são do Traefik):
`security.W004` (HSTS) e `security.W008` (SECURE_SSL_REDIRECT).
