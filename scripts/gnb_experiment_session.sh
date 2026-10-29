#!/usr/bin/env bash
# Sessão completa Tower02: diagnóstico kernel → isolamento → core → gNB.
#
# Uso:
#   sudo bash scripts/gnb_experiment_session.sh
#   sudo bash scripts/gnb_experiment_session.sh --no-restore
#   sudo bash scripts/gnb_experiment_session.sh --config configs/gnb_n78_100mhz_x310.yml
#   sudo bash scripts/gnb_experiment_session.sh --prepare-only   # só isolate + open5gs
#
# O gNB roda em foreground (Ctrl+C para parar). Com --no-restore o host permanece isolado.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CFG="${GNB_CONFIG:-configs/gnb_n78_100mhz_x310.yml}"
[[ "$CFG" != /* ]] && CFG="$ROOT/$CFG"
RESTORE=1
PREPARE_ONLY=0
SWAPOFF=0
CPUSET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CFG="$2"; shift 2 ;;
    --cpus) CPUSET="$2"; shift 2 ;;
    --swapoff) SWAPOFF=1; shift ;;
    --no-restore) RESTORE=0; shift ;;
    --prepare-only) PREPARE_ONLY=1; shift ;;
    -h|--help)
      sed -n '1,16p' "$0"
      exit 0
      ;;
    *) echo "Arg desconhecido: $1" >&2; exit 1 ;;
  esac
done

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Execute com sudo: sudo bash scripts/gnb_experiment_session.sh" >&2
  exit 1
fi

echo "=== gNB experiment session — $(hostname) ==="
echo "Config: $CFG"
echo

echo "=== 1) Diagnóstico kernel ==="
bash scripts/check_kernel_rt.sh
echo

echo "=== 2) Isolar host (--net) ==="
ISO_ARGS=(--net)
[[ "$SWAPOFF" -eq 1 ]] && ISO_ARGS+=(--swapoff)
[[ -n "$CPUSET" ]] && ISO_ARGS+=(--cpus "$CPUSET")
bash scripts/gnb_isolate_on.sh "${ISO_ARGS[@]}"

cleanup() {
  if [[ "$RESTORE" -eq 1 ]]; then
    echo
    echo "=== Restore host ==="
    bash scripts/gnb_isolate_off.sh || true
  else
    echo
    echo "[INFO] Host permanece isolado (--no-restore)."
    echo "       Restore manual: sudo bash scripts/gnb_isolate_off.sh"
  fi
}
trap cleanup EXIT

echo
echo "=== 3) Open5GS core ==="
bash scripts/open5gs_up.sh

if [[ "$PREPARE_ONLY" -eq 1 ]]; then
  echo
  echo "[OK] Host preparado. Em outro terminal:"
  echo "  sudo bash scripts/gnb_run.sh $CFG"
  echo "  bash scripts/gnb_rt_monitor.sh --follow"
  trap - EXIT
  cleanup
  exit 0
fi

# CPU hint do state file
STATE="/var/run/cerise-gnb-isolate/state.env"
if [[ -z "$CPUSET" && -f "$STATE" ]]; then
  CPUSET="$(grep -m1 '^gnb_cpuset=' "$STATE" 2>/dev/null | cut -d= -f2- | tr -d "'\"" || true)"
fi
[[ -n "$CPUSET" ]] && export GNB_CPUSET="$CPUSET"

echo
echo "=== 4) gNB (foreground) ==="
echo "Monitor RT (outro terminal): bash scripts/gnb_rt_monitor.sh --follow"
echo "Ctrl+C encerra gNB e restaura host (salvo --no-restore)."
echo

bash scripts/gnb_run.sh "$CFG"
