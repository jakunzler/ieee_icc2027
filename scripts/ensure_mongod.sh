#!/usr/bin/env bash
# Garante mongod saudável para Open5GS (UDR / WebUI / assinantes).
#
# Corrige causas típicas de falha na Tower02:
#   - drop-in/wrapper residual (kernel7)
#   - WiredTiger.turtle / dbPath com dono root (exit 14)
#   - socket /tmp/mongodb-27017.sock órfão
#   - mongod.lock stale sem processo
#   - kernel ≥6.19 + MongoDB 8.0.26 (SERVER-121912) → avisa / bypass opcional
#
# Uso:
#   sudo bash scripts/ensure_mongod.sh
#   sudo bash scripts/ensure_mongod.sh --check-only
#   sudo bash scripts/ensure_mongod.sh --with-webui
#   sudo bash scripts/ensure_mongod.sh --allow-unsupported-kernel   # só se boot ≥6.19 inevitável
#
# NÃO altera /etc/mongod.conf. Só ownership, locks, unit drop-in residual e start.

set -euo pipefail

CHECK_ONLY=0
WITH_WEBUI=0
ALLOW_BAD_KERNEL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only|-c) CHECK_ONLY=1; shift ;;
    --with-webui) WITH_WEBUI=1; shift ;;
    --allow-unsupported-kernel) ALLOW_BAD_KERNEL=1; shift ;;
    -h|--help) sed -n '1,22p' "$0"; exit 0 ;;
    *) echo "Arg desconhecido: $1" >&2; exit 1 ;;
  esac
done

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Execute com sudo: sudo bash $0" >&2
  exit 1
fi

KVER="$(uname -r)"
KMAJ="$(echo "$KVER" | cut -d. -f1)"
KMIN="$(echo "$KVER" | cut -d. -f2)"
DBPATH="$(awk '/^[[:space:]]*dbPath:/{print $2; exit}' /etc/mongod.conf 2>/dev/null || echo /var/lib/mongodb)"
LOGPATH="$(awk '/^[[:space:]]*path:/{print $2; exit}' /etc/mongod.conf 2>/dev/null || echo /var/log/mongodb/mongod.log)"
LOGDIR="$(dirname "$LOGPATH")"
SOCK=/tmp/mongodb-27017.sock
DROP_DIR=/etc/systemd/system/mongod.service.d
DROP="$DROP_DIR/99-kernel7-workaround.conf"
WRAPPER=/usr/local/sbin/mongod-kernel7-wrapper.sh
DEFAULT=/etc/default/mongod

kernel_bad_for_mongo() {
  [[ "$KMAJ" -gt 6 || ( "$KMAJ" -eq 6 && "$KMIN" -ge 19 ) ]]
}

mongod_ping() {
  mongosh --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1
}

echo "=== ensure_mongod — $(hostname) — kernel $KVER ==="

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "mongod unit: $(systemctl is-active mongod 2>/dev/null || echo '?')"
  mongod_ping && echo "ping: OK" || echo "ping: FALHOU"
  ss -ltn 2>/dev/null | grep -q ':27017' && echo "porta 27017: aberta" || echo "porta 27017: fechada"
  [[ -f "$DROP" ]] && echo "[WARN] drop-in residual: $DROP"
  find "$DBPATH" -maxdepth 1 ! -user mongodb 2>/dev/null | head -5 | sed 's/^/[WARN] dono ≠ mongodb: /' || true
  if kernel_bad_for_mongo; then
    echo "[WARN] Kernel ≥6.19 — MongoDB 8.0.26 pode recusar start (SERVER-121912)."
    echo "       Preferir GRUB 6.17.0-38-generic"
  fi
  exit 0
fi

# --- 1) Kernel ---
if kernel_bad_for_mongo && [[ "$ALLOW_BAD_KERNEL" -ne 1 ]]; then
  echo "[ERRO] Kernel $KVER ≥6.19 incompatível com mongod 8.0.26 sem bypass."
  echo "       Boot recomendado: 6.17.0-38-generic (já no GRUB_DEFAULT)."
  echo "       Ou: sudo bash $0 --allow-unsupported-kernel"
  exit 2
fi

# --- 2) Remover workaround residual (sempre no fluxo normal / 6.17) ---
if [[ -f "$DROP" || -f "$WRAPPER" ]]; then
  echo "[1] Removendo drop-in/wrapper residual..."
  rm -f "$DROP" "$WRAPPER"
  rmdir "$DROP_DIR" 2>/dev/null || true
  NEED_RELOAD=1
else
  echo "[1] Sem drop-in/wrapper residual"
  NEED_RELOAD=0
fi

# Limpar só a chave de bypass que nós adicionamos (não mexe no unit oficial)
if [[ -f "$DEFAULT" ]]; then
  if grep -q '^MONGODB_ALLOW_UNSUPPORTED_KERNEL=' "$DEFAULT" 2>/dev/null; then
    if [[ "$ALLOW_BAD_KERNEL" -eq 1 ]]; then
      sed -i 's/^MONGODB_ALLOW_UNSUPPORTED_KERNEL=.*/MONGODB_ALLOW_UNSUPPORTED_KERNEL=1/' "$DEFAULT"
    else
      sed -i '/^MONGODB_ALLOW_UNSUPPORTED_KERNEL=/d' "$DEFAULT"
    fi
  elif [[ "$ALLOW_BAD_KERNEL" -eq 1 ]]; then
    echo 'MONGODB_ALLOW_UNSUPPORTED_KERNEL=1' >>"$DEFAULT"
  fi
fi

# Bypass mínimo via Environment= (não substitui ExecStart)
if [[ "$ALLOW_BAD_KERNEL" -eq 1 ]]; then
  mkdir -p "$DROP_DIR"
  cat >"$DROP" <<'EOF'
[Service]
# SERVER-121912 — só Environment; NÃO altera ExecStart
Environment=MONGODB_ALLOW_UNSUPPORTED_KERNEL=1
EOF
  NEED_RELOAD=1
  echo "[1b] Drop-in mínimo (ALLOW_UNSUPPORTED_KERNEL) instalado"
fi

[[ "$NEED_RELOAD" -eq 1 ]] && systemctl daemon-reload

# --- 3) Parar limpo ---
echo "[2] Parando mongod / órfãos..."
systemctl stop mongod.service 2>/dev/null || true
systemctl reset-failed mongod.service 2>/dev/null || true
# Aguardar sair
for _ in $(seq 1 20); do
  pgrep -x mongod >/dev/null 2>&1 || break
  sleep 0.25
done
if pgrep -x mongod >/dev/null 2>&1; then
  echo "  SIGTERM mongod órfão..."
  pkill -TERM -x mongod 2>/dev/null || true
  sleep 1
fi
if pgrep -x mongod >/dev/null 2>&1; then
  echo "  SIGKILL mongod órfão..."
  pkill -KILL -x mongod 2>/dev/null || true
  sleep 0.5
fi

# --- 4) Socket / lock stale ---
echo "[3] Limpando socket/lock stale..."
if [[ -e "$SOCK" ]] && ! pgrep -x mongod >/dev/null 2>&1; then
  rm -f "$SOCK"
  echo "  removido $SOCK"
fi
if [[ -f "$DBPATH/mongod.lock" ]] && ! pgrep -x mongod >/dev/null 2>&1; then
  # lock vazio ou PID morto → seguro remover
  rm -f "$DBPATH/mongod.lock"
  echo "  removido $DBPATH/mongod.lock"
fi
if [[ -f "$DBPATH/WiredTiger.lock" ]] && ! pgrep -x mongod >/dev/null 2>&1; then
  # WiredTiger.lock residual também impede reopen
  # Conteúdo típico: PID; se processo morto, remove
  rm -f "$DBPATH/WiredTiger.lock"
  echo "  removido $DBPATH/WiredTiger.lock"
fi

# --- 5) Ownership (causa clássica exit 14: WiredTiger.turtle root:root) ---
echo "[4] Corrigindo ownership mongodb:mongodb em $DBPATH e $LOGDIR..."
if id mongodb >/dev/null 2>&1; then
  BAD="$(find "$DBPATH" "$LOGDIR" -maxdepth 2 \( ! -user mongodb -o ! -group mongodb \) 2>/dev/null | head -20 || true)"
  if [[ -n "$BAD" ]]; then
    echo "$BAD" | sed 's/^/  fix: /'
  fi
  chown -R mongodb:mongodb "$DBPATH" "$LOGDIR"
  # permissões mínimas seguros
  chmod 755 "$DBPATH" "$LOGDIR" 2>/dev/null || true
else
  echo "[ERRO] usuário mongodb inexistente"
  exit 1
fi

# --- 6) Start ---
echo "[5] Iniciando mongod.service..."
systemctl reset-failed mongod.service 2>/dev/null || true
if ! systemctl start mongod.service; then
  echo "[ERRO] systemctl start falhou"
  journalctl -u mongod -n 30 --no-pager || true
  echo "--- últimos erros no log ---"
  tail -40 "$LOGPATH" 2>/dev/null || true
  exit 1
fi

ok=0
for i in $(seq 1 60); do
  if systemctl is-active --quiet mongod.service && mongod_ping; then
    ok=1
    break
  fi
  # se falhou rápido, não ficar 30s em loop cego
  if ! systemctl is-active --quiet mongod.service && [[ "$i" -gt 6 ]]; then
    break
  fi
  sleep 0.5
done

if [[ "$ok" -ne 1 ]]; then
  echo "[ERRO] mongod não ficou ready"
  systemctl status mongod --no-pager -l | head -25 || true
  journalctl -u mongod -n 20 --no-pager || true
  echo "--- $LOGPATH ---"
  tail -50 "$LOGPATH" 2>/dev/null || true
  exit 1
fi

echo "[OK] mongod active — ping OK (127.0.0.1:27017)"

# Contagem rápida de coleções open5gs (não falha se DB vazio)
if command -v mongosh >/dev/null 2>&1; then
  ncols="$(mongosh --quiet --eval 'db.getSiblingDB("open5gs").getCollectionNames().length' 2>/dev/null || echo '?')"
  echo "     DB open5gs: $ncols coleção(ões)"
fi

if [[ "$WITH_WEBUI" -eq 1 ]] && systemctl cat open5gs-webui.service >/dev/null 2>&1; then
  echo "[6] open5gs-webui..."
  systemctl restart open5gs-webui.service 2>/dev/null || systemctl start open5gs-webui.service
  sleep 2
  if systemctl is-active --quiet open5gs-webui.service; then
    echo "[OK] WebUI http://localhost:9999  (admin / 1423)"
  else
    echo "[WARN] webui não active — journalctl -u open5gs-webui -n 20"
  fi
fi

echo
echo "Próximo: sudo bash scripts/open5gs_up.sh"
