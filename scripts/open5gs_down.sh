#!/usr/bin/env bash
# Para o núcleo Open5GS 5G SA iniciado por open5gs_up.sh.
# Uso:  sudo bash scripts/open5gs_down.sh
#       sudo bash scripts/open5gs_down.sh --with-webui
#       sudo bash scripts/open5gs_down.sh --with-mongod   # raramente necessário
#
# Por padrão NÃO para mongod (preserva assinantes / evita lock stale).

set -euo pipefail

STOP_WEBUI=0
STOP_MONGOD=0
[[ "${1:-}" == "--with-webui" || "${2:-}" == "--with-webui" ]] && STOP_WEBUI=1
[[ "${1:-}" == "--with-mongod" || "${2:-}" == "--with-mongod" ]] && STOP_MONGOD=1

SA_UNITS=(
  open5gs-upfd
  open5gs-bsfd
  open5gs-nssfd
  open5gs-pcfd
  open5gs-udrd
  open5gs-udmd
  open5gs-ausfd
  open5gs-smfd
  open5gs-amfd
  open5gs-scpd
  open5gs-nrfd
)

_sc() {
  if [[ "${EUID}" -ne 0 ]]; then
    sudo systemctl "$@"
  else
    systemctl "$@"
  fi
}

unit_loaded() {
  systemctl cat --no-pager "$1" >/dev/null 2>&1
}

ok=0
for u in "${SA_UNITS[@]}"; do
  us="${u}.service"
  unit_loaded "$us" || continue
  printf 'Parando %s ... ' "$us"
  if _sc stop "$us"; then
    echo "OK"
    ok=$((ok + 1))
  else
    echo "FALHOU (ignorado)"
  fi
done

if [[ "$STOP_WEBUI" -eq 1 ]] && unit_loaded open5gs-webui.service; then
  printf 'Parando open5gs-webui ... '
  _sc stop open5gs-webui.service && echo OK || echo FALHOU
fi

if [[ "$STOP_MONGOD" -eq 1 ]]; then
  printf 'Parando mongod ... '
  _sc stop mongod.service && echo OK || echo FALHOU
else
  echo "mongod: mantido active (use --with-mongod para parar)"
fi

echo "Parados: $ok NFs SA"
echo "Open5GS SA DOWN."
