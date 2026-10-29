#!/usr/bin/env bash
# Restaura serviços/VM após gnb_isolate_on.sh na Tower02.
# Uso: sudo bash scripts/gnb_isolate_off.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lab_isolate_common.inc.sh"

lab_isolate_require_root

STATE="$(lab_isolate_state_file)"
if ! lab_isolate_is_active; then
  echo "Nenhum isolamento gNB ativo ($STATE)."
  exit 0
fi

echo "=== gNB isolate OFF — $(hostname) ==="

echo "[1] Restaurar VM / swap / KMS"
lab_isolate_restore_vm_from_state "$STATE"

echo "[2] Restaurar serviços parados"
lab_isolate_restore_units_from_state "$STATE"

rm -f "$STATE"
rmdir "$(lab_isolate_state_dir)" 2>/dev/null || true

echo
echo "[OK] Host restaurado. Open5GS não foi reiniciado — verifique com:"
echo "     bash scripts/network_jammer_status.sh"
