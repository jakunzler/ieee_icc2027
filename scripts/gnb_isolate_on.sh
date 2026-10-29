#!/usr/bin/env bash
# Isola a Tower02 para experimentos gNB + Open5GS + jammer.
# Para serviços concorrentes (docker, cups, …) mas MANTÉM Open5GS/mongod.
#
# NÃO use watchdog_isolate_on.sh na Tower02 — ele derruba o core 5G.
#
# Uso:
#   sudo bash scripts/gnb_isolate_on.sh
#   sudo bash scripts/gnb_isolate_on.sh --net          # + tune enp2s0 jumbo
#   sudo bash scripts/gnb_isolate_on.sh --net --swapoff
#   sudo bash scripts/gnb_isolate_on.sh --cpus 2-7      # hint para taskset no gnb_run
#
# Restore: sudo bash scripts/gnb_isolate_off.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lab_isolate_common.inc.sh"
# shellcheck disable=SC1091
[[ -f "$ROOT/configs/lab_addrs.env" ]] && source "$ROOT/configs/lab_addrs.env"

lab_isolate_require_root

DO_NET=0
DO_SWAPOFF=0
GNB_CPUSET="${GNB_CPUSET:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --net) DO_NET=1; shift ;;
    --swapoff) DO_SWAPOFF=1; shift ;;
    --cpus) GNB_CPUSET="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,18p' "$0"
      exit 0
      ;;
    *) echo "Arg desconhecido: $1" >&2; exit 1 ;;
  esac
done

STATE="$(lab_isolate_state_file)"
if lab_isolate_is_active; then
  echo "[WARN] Isolamento gNB já ativo ($STATE). Rode gnb_isolate_off.sh antes de reativar."
  exit 1
fi

lab_isolate_ensure_state_dir
: >"$STATE"
lab_isolate_record "started_at" "$(date -Iseconds)"
lab_isolate_record "host" "$(hostname)"

echo "=== gNB isolate ON — $(hostname) ==="
echo "Open5GS / mongod NÃO serão parados."
echo

echo "[0] Kernel / preempção"
bash "$ROOT/scripts/check_kernel_rt.sh" | sed -n '1,25p' || true
echo

echo "[1] srsRAN performance (governor + KMS polling)"
lab_isolate_apply_srsran_performance

echo "[2] VM / latência"
lab_isolate_tune_vm
if [[ "$DO_SWAPOFF" -eq 1 ]]; then
  lab_isolate_disable_swap
fi

echo "[3] IRQ balance"
lab_isolate_stop_irqbalance

echo "[4] Serviços concorrentes"
lab_isolate_stop_competing

if [[ "$DO_NET" -eq 1 ]]; then
  echo "[5] Rede host↔X310 gNB"
  bash "$ROOT/scripts/tune_gnb_host_net.sh" "${GNB_IFACE:-enp2s0}"
else
  echo "[5] Rede: pulado (use --net para MTU jumbo + buffers)"
fi

if [[ -n "$GNB_CPUSET" ]]; then
  lab_isolate_record "gnb_cpuset" "$GNB_CPUSET"
  echo "[6] CPU hint gravado: GNB_CPUSET=$GNB_CPUSET"
  echo "    Exporte antes do gNB: export GNB_CPUSET=$GNB_CPUSET"
else
  echo "[6] CPU hint: nenhum (--cpus N-M para reservar núcleos ao gNB)"
fi

echo
echo "[OK] Host isolado para experimento gNB."
echo "     sudo bash scripts/open5gs_up.sh   # se core ainda não estiver up"
echo "     sudo bash scripts/gnb_run.sh"
echo "     bash scripts/gnb_rt_monitor.sh --follow"
echo "     Restore: sudo bash scripts/gnb_isolate_off.sh"
