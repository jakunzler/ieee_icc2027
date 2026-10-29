#!/usr/bin/env bash
# Isola a Tower03 para captura watchdog (para Open5GS local + serviços ruidosos, sobe CPU).
# Na Tower02 (gNB + Open5GS) use gnb_isolate_on.sh.
#
# NÃO para ModemManager — o UE Quectel nesta WS depende dele para dados.
# Open5GS/mongod/ogstun locais SÃO parados (core não fica nesta máquina no fluxo watchdog).
#
# Uso:
#   sudo bash scripts/watchdog_isolate_on.sh
#   # ... captura / fig1 ...
#   sudo bash scripts/watchdog_isolate_off.sh
#
# Opcional: também roda tune de rede
#   sudo bash scripts/watchdog_isolate_on.sh --net

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="/var/tmp/watchdog_isolate_state"
IFACE="${WATCHDOG_IFACE:-enp1s0}"
DO_NET=0

for arg in "$@"; do
  case "$arg" in
    --net) DO_NET=1 ;;
    -h|--help)
      sed -n '1,22p' "$0"
      exit 0
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute com sudo: sudo bash $0 [--net]"
  exit 1
fi

mkdir -p "$STATE_DIR"

# Serviços que competem por CPU/IO — NÃO incluir ModemManager (UE depende).
SERVICES=(
  open5gs-amfd open5gs-ausfd open5gs-bsfd open5gs-hssd open5gs-mmed
  open5gs-nrfd open5gs-nssfd open5gs-pcfd open5gs-pcrfd open5gs-scpd
  open5gs-seppd open5gs-sgwcd open5gs-sgwud open5gs-smfd open5gs-udmd
  open5gs-udrd open5gs-upfd open5gs-webui
  mongod
  docker
  containerd
  snap.canonical-livepatch.canonical-livepatchd
  cups cups-browsed
  avahi-daemon
)

echo "[isolate] salvando estado em $STATE_DIR"
: > "$STATE_DIR/stopped_services.txt"
: > "$STATE_DIR/governors.txt"

# --- governors ---
shopt -s nullglob
for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
  echo "$(basename "$(dirname "$(dirname "$g")")")=$(cat "$g")" >> "$STATE_DIR/governors.txt"
  echo performance > "$g" 2>/dev/null || true
done
echo "[isolate] CPU governor → performance"

# --- power profile (se existir) ---
if command -v powerprofilesctl &>/dev/null; then
  powerprofilesctl get > "$STATE_DIR/power_profile.txt" 2>/dev/null || true
  powerprofilesctl set performance 2>/dev/null || true
fi

# --- stop noisy services ---
for svc in "${SERVICES[@]}"; do
  if systemctl is-active --quiet "${svc}.service" 2>/dev/null; then
    echo "$svc" >> "$STATE_DIR/stopped_services.txt"
    systemctl stop "${svc}.service" && echo "  stop ${svc}"
  fi
done

# ogstun some vezes sobra — não derruba interfaces USRP
if ip link show ogstun &>/dev/null; then
  ip link set ogstun down 2>/dev/null || true
  echo "[isolate] ogstun down"
fi

# drop caches leves (ajuda jitter de escrita no início da captura)
sync
echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true

if [[ "$DO_NET" -eq 1 ]]; then
  echo "[isolate] aplicando tune de rede..."
  bash "$ROOT/scripts/tune_usrp_host_net.sh" "$IFACE"
fi

# NIC IRQ coalesce mais agressivo (menos interrupções → menos overhead)
if command -v ethtool &>/dev/null; then
  ethtool -C "$IFACE" rx-usecs 50 2>/dev/null || true
fi

echo
echo "[isolate] ON"
echo "  link: $(ip -br link show "$IFACE" 2>/dev/null || true)"
echo "  open5gs ativos: $(systemctl list-units 'open5gs*' --state=running --no-legend 2>/dev/null | wc -l)"
echo "  ModemManager: $(systemctl is-active ModemManager 2>/dev/null || true) (preservado p/ UE)"
echo "  docker: $(systemctl is-active docker 2>/dev/null || true)"
echo "  governors sample: $(head -1 "$STATE_DIR/governors.txt" | cut -d= -f2) → performance"
echo
echo "Próximo (como usuário):"
echo "  cd $ROOT"
echo "  export PYTHONPATH=\"\$PWD/src:\$PYTHONPATH\""
echo "  /usr/bin/python3 -m ref_repro.capture.watchdog_capture \\"
echo "      # 184.32e6 precisa de master_clock_rate=184.32e6 (watchdog_capture já infere)"
echo "      --case A --seconds 20 --fs 184.32e6 --fc 3.750e9 --gain 20 \\"
echo "      --addr 192.168.40.2 --out data/raw/fig1"
echo
echo "Depois: sudo bash scripts/watchdog_isolate_off.sh"
