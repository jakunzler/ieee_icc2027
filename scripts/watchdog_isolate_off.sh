#!/usr/bin/env bash
# Restaura serviços parados por watchdog_isolate_on.sh e governors anteriores.
#
# Uso:
#   sudo bash scripts/watchdog_isolate_off.sh

set -euo pipefail

STATE_DIR="/var/tmp/watchdog_isolate_state"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute com sudo: sudo bash $0"
  exit 1
fi

if [[ ! -d "$STATE_DIR" ]]; then
  echo "[isolate-off] sem estado em $STATE_DIR — nada a restaurar de sessão"
  echo "             (reinicie open5gs/docker manualmente se necessário)"
  exit 0
fi

echo "[isolate-off] restaurando CPU governors..."
if [[ -f "$STATE_DIR/governors.txt" ]]; then
  while IFS='=' read -r cpu gov; do
    [[ -z "${cpu:-}" || -z "${gov:-}" ]] && continue
    path="/sys/devices/system/cpu/${cpu}/cpufreq/scaling_governor"
    if [[ -f "$path" ]]; then
      echo "$gov" > "$path" 2>/dev/null || true
    fi
  done < "$STATE_DIR/governors.txt"
fi

if [[ -f "$STATE_DIR/power_profile.txt" ]] && command -v powerprofilesctl &>/dev/null; then
  powerprofilesctl set "$(cat "$STATE_DIR/power_profile.txt")" 2>/dev/null || true
fi

echo "[isolate-off] religando serviços que foram parados nesta sessão..."
if [[ -f "$STATE_DIR/stopped_services.txt" ]]; then
  # docker/containerd: subir containerd antes do docker
  mapfile -t stopped < "$STATE_DIR/stopped_services.txt"
  # ordena: containerd → docker → resto
  ordered=()
  for s in containerd docker; do
    for x in "${stopped[@]}"; do
      [[ "$x" == "$s" ]] && ordered+=("$s")
    done
  done
  for x in "${stopped[@]}"; do
    [[ "$x" == "containerd" || "$x" == "docker" ]] && continue
    ordered+=("$x")
  done
  for svc in "${ordered[@]}"; do
    [[ -z "$svc" ]] && continue
    systemctl start "${svc}.service" && echo "  start ${svc}" || echo "  [WARN] falha start ${svc}"
  done
fi

# limpa estado da sessão
rm -rf "$STATE_DIR"
echo "[isolate-off] OFF — sessão restaurada."
