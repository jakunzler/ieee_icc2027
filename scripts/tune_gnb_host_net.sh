#!/usr/bin/env bash
# Tunning de rede host↔USRP X310 (gNB) na Tower02.
# Uso:
#   sudo bash scripts/tune_gnb_host_net.sh
#   sudo bash scripts/tune_gnb_host_net.sh enp2s0
#
# Interface padrão: enp2s0 (192.168.40.5 ↔ X310 gNB 192.168.40.3)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$ROOT/configs/lab_addrs.env" ]] && source "$ROOT/configs/lab_addrs.env"

IFACE="${1:-${GNB_IFACE:-enp2s0}}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute com sudo: sudo bash $0 [$IFACE]"
  exit 1
fi

if ! ip link show "$IFACE" &>/dev/null; then
  echo "Interface $IFACE não encontrada"
  exit 1
fi

echo "[1/5] Governor CPU → performance"
if command -v cpupower >/dev/null 2>&1; then
  cpupower frequency-set -g performance >/dev/null || true
elif [[ -d /sys/devices/system/cpu ]]; then
  for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo performance >"$g" 2>/dev/null || true
  done
fi

echo "[2/5] Link UP + MTU jumbo 9000 em $IFACE"
ip link set "$IFACE" up
ip link set "$IFACE" mtu 9000

echo "[3/5] Buffers de socket do kernel (RX/TX)"
sysctl -w net.core.rmem_max=268435456
sysctl -w net.core.wmem_max=268435456
sysctl -w net.core.rmem_default=67108864
sysctl -w net.core.wmem_default=67108864
sysctl -w net.core.netdev_max_backlog=50000
sysctl -w net.ipv4.udp_mem="8388608 16777216 33554432" || true

echo "[4/5] Ring buffers da NIC (máximo)"
ethtool -G "$IFACE" rx 8184 tx 8184 || echo "[WARN] ethtool -G falhou (driver pode limitar)"

echo "[5/5] Persistência em /etc/sysctl.d/99-usrp-gnb-tower02.conf"
cat > /etc/sysctl.d/99-usrp-gnb-tower02.conf <<'EOF'
# USRP X310 gNB / UHD — host 10GbE (Tower02 rede+jammer)
net.core.rmem_max = 268435456
net.core.wmem_max = 268435456
net.core.rmem_default = 67108864
net.core.wmem_default = 67108864
net.core.netdev_max_backlog = 50000
EOF

echo
echo "Estado:"
ip -br addr show "$IFACE"
sysctl net.core.rmem_max net.core.wmem_max net.core.netdev_max_backlog
ethtool -g "$IFACE" 2>/dev/null | sed -n '/Current hardware/,+6p' || true
echo
echo "[INFO] Se o gNB reportar frame size 1488 com MTU 9000, confira switch/cabo 10GbE"
echo "       e reexecute este script; depois: sudo bash scripts/gnb_run.sh"
