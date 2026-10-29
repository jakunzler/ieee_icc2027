#!/usr/bin/env bash
# Tunning de rede host↔USRP X310 (10GbE) para capturas altas (100–200 MS/s).
# Uso (Tower03):
#   sudo bash scripts/tune_usrp_host_net.sh
#
# Interface padrão: enp1s0 (192.168.40.3 ↔ X310 192.168.40.2)

set -euo pipefail

IFACE="${1:-enp1s0}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute com sudo: sudo bash $0 [$IFACE]"
  exit 1
fi

if ! ip link show "$IFACE" &>/dev/null; then
  echo "Interface $IFACE não encontrada"
  exit 1
fi

echo "[1/4] MTU jumbo 9000 em $IFACE"
ip link set "$IFACE" mtu 9000

echo "[2/4] Buffers de socket do kernel (RX/TX)"
# ~256 MiB — suficiente p/ UHD pedir ~25 MiB e sobrar folga a 200 MS/s
sysctl -w net.core.rmem_max=268435456
sysctl -w net.core.wmem_max=268435456
sysctl -w net.core.rmem_default=67108864
sysctl -w net.core.wmem_default=67108864
sysctl -w net.core.netdev_max_backlog=50000
sysctl -w net.ipv4.udp_mem="8388608 16777216 33554432" || true

echo "[3/4] Ring buffers da NIC (máximo)"
# enp1s0: max RX/TX 8184
ethtool -G "$IFACE" rx 8184 tx 8184 || echo "[WARN] ethtool -G falhou (driver pode limitar)"

echo "[4/4] Persistência opcional em /etc/sysctl.d/99-usrp-x310.conf"
cat > /etc/sysctl.d/99-usrp-x310.conf <<'EOF'
# USRP X310 / UHD — host 10GbE (Tower03 watchdog)
net.core.rmem_max = 268435456
net.core.wmem_max = 268435456
net.core.rmem_default = 67108864
net.core.wmem_default = 67108864
net.core.netdev_max_backlog = 50000
EOF

echo
echo "Estado:"
ip -br link show "$IFACE"
sysctl net.core.rmem_max net.core.wmem_max net.core.netdev_max_backlog
ethtool -g "$IFACE" | sed -n '/Current hardware/,+6p'
echo
echo "OK. MTU jumbo + buffers aplicados. Rings e MTU podem precisar de NetworkManager/"
echo "systemd-networkd se a interface for recriada — reexecute este script após reboot se necessário."
