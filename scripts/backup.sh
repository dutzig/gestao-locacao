#!/usr/bin/env bash
# Backup semanal: cópia consistente do SQLite feita dentro do container, enviada
# por pipe direto ao age (o texto claro nunca toca o disco do host) e então ao
# Telegram. Roda no host como root (cron). Variáveis vêm do backup.env.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
set -a; . ./backup.env; set +a
: "${AGE_RECIPIENT:?}" "${TG_TOKEN:?}" "${TG_CHAT:?}"

# Qualquer falha daqui em diante avisa no Telegram (token por stdin, como no envio do arquivo).
avisar_falha() {
  printf 'url = "https://api.telegram.org/bot%s/sendMessage"\ndata-urlencode = "chat_id=%s"\ndata-urlencode = "text=Backup falhou (%s). Veja /var/log/gestao-backup.log."\n' \
    "$TG_TOKEN" "$TG_CHAT" "$(date +%d/%m/%Y)" | curl -sS --fail -o /dev/null -K - || true
}
trap avisar_falha ERR

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
arq="$tmp/gestao-$(date +%Y%m%d).sqlite3.age"

code='
import os, shutil, sqlite3, sys
tmp = "/tmp/backup.sqlite3"
try:
    src = sqlite3.connect(os.environ["DB_PATH"])
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    dst.close(); src.close()
    with open(tmp, "rb") as f:
        shutil.copyfileobj(f, sys.stdout.buffer)
finally:
    if os.path.exists(tmp):
        os.remove(tmp)
'
docker compose exec -T gestao python -c "$code" | age -r "$AGE_RECIPIENT" -o "$arq"

# URL (com o token) vai por stdin: não aparece em argv/ps nem é ecoada.
printf 'url = "https://api.telegram.org/bot%s/sendDocument"\nform-string = "chat_id=%s"\nform = "document=@%s"\n' \
  "$TG_TOKEN" "$TG_CHAT" "$arq" | curl -sS --fail -o /dev/null -K -
