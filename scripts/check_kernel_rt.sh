#!/usr/bin/env bash
# Diagnóstico de kernel / preempção para srsRAN gNB (Tower02).
# Uso:
#   bash scripts/check_kernel_rt.sh
#   sudo bash scripts/check_kernel_rt.sh   # inclui /sys/kernel/debug/sched/preempt
#
# Referência: srsRAN Performance Tuning / Troubleshooting.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lab_isolate_common.inc.sh"

KVER="$(uname -r)"
KCFG="/boot/config-${KVER}"

echo "=== Kernel RT / preempção — $(hostname) — $KVER ==="
echo

if [[ -f "$KCFG" ]]; then
  echo "[1] /boot/config-${KVER}"
  grep -E '^CONFIG_PREEMPT|^CONFIG_HZ=' "$KCFG" || true
  grep -E '^CONFIG_PREEMPT_RT=|^# CONFIG_PREEMPT_RT' "$KCFG" || true
  grep -E '^CONFIG_PREEMPT=|^# CONFIG_PREEMPT ' "$KCFG" 2>/dev/null || true
else
  echo "[1] $KCFG não encontrado"
fi
echo

echo "[2] Linha de boot (/proc/cmdline)"
cat /proc/cmdline
echo
echo

echo "[3] Modo de preempção efetivo"
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  lab_isolate_read_preempt_mode
  echo
  echo "  Interpretação: 'full' ou '(full)' = maior preempção sem PREEMPT_RT."
  echo "  Ex.: 'full (lazy)' ou 'none voluntary (full)' — ambos OK para laboratório."
else
  echo "  Requer root para debugfs. Rode: sudo bash scripts/check_kernel_rt.sh"
fi
echo

echo "[4] Governor CPU (amostra cpu0)"
if [[ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]]; then
  cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
else
  echo "  cpufreq não disponível"
fi
echo

echo "[5] Recomendação Tower02 (gNB 100 MHz 2×2)"
if grep -q '^CONFIG_PREEMPT_RT=y' "$KCFG" 2>/dev/null; then
  echo "  PREEMPT_RT instalado — bom para determinismo máximo."
elif grep -q '^CONFIG_PREEMPT_DYNAMIC=y' "$KCFG" 2>/dev/null; then
  echo "  PREEMPT_DYNAMIC — confirme modo (full) acima antes dos experimentos."
  echo "  Instale PREEMPT_RT somente se late/underflow/overflow persistirem no gNB."
else
  echo "  Kernel sem PREEMPT_DYNAMIC — considere kernel com preempt=full ou PREEMPT_RT."
fi
echo
echo "  Próximo passo:"
echo "    sudo bash scripts/gnb_isolate_on.sh --net"
echo "    sudo bash scripts/open5gs_up.sh"
echo "    sudo bash scripts/gnb_run.sh"
echo "    bash scripts/gnb_rt_monitor.sh --log data/logs/gnb.log"
