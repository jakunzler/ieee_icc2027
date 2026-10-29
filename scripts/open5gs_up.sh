#!/usr/bin/env bash
# Sobe o núcleo Open5GS 5G SA (sem EPC) na Tower02.
# Uso:  sudo bash scripts/open5gs_up.sh
#       sudo bash scripts/open5gs_up.sh --no-webui
#
# Ordem: ensure_mongod → NFs SA → ogstun/NAT → WebUI
# Sem mongod saudável o UDR não sobe e o AMF rejeita UE (HTTP 504 / cause 90).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$ROOT/configs/lab_addrs.env" ]] && source "$ROOT/configs/lab_addrs.env"

UE_SUBNET="${UE_SUBNET:-10.45.0.0/16}"
START_WEBUI=1
[[ "${1:-}" == "--no-webui" ]] && START_WEBUI=0

# NFs 5G SA (não inicia EPC: mme/hss/sgwc/sgwu/pcrf)
SA_UNITS=(
  open5gs-nrfd
  open5gs-scpd
  open5gs-amfd
  open5gs-smfd
  open5gs-ausfd
  open5gs-udmd
  open5gs-udrd
  open5gs-pcfd
  open5gs-nssfd
  open5gs-bsfd
  open5gs-upfd
)

_sc() {
  if [[ "${EUID}" -ne 0 ]]; then
    sudo systemctl "$@"
  else
    systemctl "$@"
  fi
}

_run() {
  if [[ "${EUID}" -ne 0 ]]; then
    sudo "$@"
  else
    "$@"
  fi
}

unit_loaded() {
  systemctl cat --no-pager "$1" >/dev/null 2>&1
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Execute com sudo: sudo bash $0" >&2
  exit 1
fi

echo "=== Open5GS SA bring-up — $(hostname) — kernel $(uname -r) ==="
echo

# --- MongoDB (obrigatório) ---
if [[ -x "$ROOT/scripts/ensure_mongod.sh" ]]; then
  bash "$ROOT/scripts/ensure_mongod.sh"
else
  echo "[ERRO] scripts/ensure_mongod.sh não encontrado"
  exit 1
fi
echo

ok=0
fail=0
missing=0
activating=0

for u in "${SA_UNITS[@]}"; do
  us="${u}.service"
  if ! unit_loaded "$us"; then
    echo "  [skip] $us (não instalado)"
    missing=$((missing + 1))
    continue
  fi
  printf 'Iniciando %s ... ' "$us"
  if _sc start "$us"; then
    state="activating"
    for _ in $(seq 1 40); do
      state=$(systemctl show -p ActiveState --value "$us" 2>/dev/null || echo "?")
      [[ "$state" != "activating" ]] && break
      sleep 0.25
    done
    if [[ "$state" == "active" ]]; then
      echo "OK"
      ok=$((ok + 1))
    elif [[ "$state" == "activating" ]]; then
      echo "ACTIVATING (ainda)"
      activating=$((activating + 1))
    else
      echo "FALHOU ($state)"
      fail=$((fail + 1))
    fi
  else
    echo "FALHOU"
    fail=$((fail + 1))
  fi
done

echo
echo "Resumo NFs: ${ok} ok, ${fail} falha(s), ${activating} activating, ${missing} ausente(s)."
echo "ActiveState:"
for u in "${SA_UNITS[@]}"; do
  us="${u}.service"
  unit_loaded "$us" || continue
  state=$(systemctl show -p ActiveState --value "$us" 2>/dev/null || echo "?")
  printf '  %-32s %s\n' "$us" "$state"
done

# UDR indispensável
if unit_loaded open5gs-udrd.service; then
  udr_state=$(systemctl show -p ActiveState --value open5gs-udrd.service 2>/dev/null || echo "?")
  if [[ "$udr_state" != "active" ]]; then
    echo
    echo "[ERRO] open5gs-udrd não active ($udr_state) — registro UE falhará."
    echo "       journalctl -u open5gs-udrd -n 30 --no-pager"
    echo "       sudo bash scripts/ensure_mongod.sh"
    fail=$((fail + 1))
  fi
fi

# PCF também usa MongoDB
if unit_loaded open5gs-pcfd.service; then
  pcf_state=$(systemctl show -p ActiveState --value open5gs-pcfd.service 2>/dev/null || echo "?")
  if [[ "$pcf_state" != "active" ]]; then
    echo "[WARN] open5gs-pcfd não active ($pcf_state) — sessões PDU podem falhar."
  fi
fi

# ogstun (UPF TUN)
if ip link show ogstun &>/dev/null; then
  echo
  echo "Subindo ogstun + IP 10.45.0.1/16 ..."
  _run ip link set ogstun up || true
  _run ip addr replace 10.45.0.1/16 dev ogstun 2>/dev/null || true
else
  echo
  echo "[WARN] Interface ogstun ainda não existe — UPF pode criar ao processar sessão."
fi

# NAT UE → Internet
echo "NAT MASQUERADE ${UE_SUBNET} ..."
_run sysctl -w net.ipv4.ip_forward=1 >/dev/null
if command -v iptables >/dev/null 2>&1; then
  if ! _run iptables -t nat -C POSTROUTING -s "$UE_SUBNET" ! -o ogstun -j MASQUERADE 2>/dev/null; then
    _run iptables -t nat -A POSTROUTING -s "$UE_SUBNET" ! -o ogstun -j MASQUERADE
  fi
  if ! _run iptables -C INPUT -i ogstun -j ACCEPT 2>/dev/null; then
    _run iptables -I INPUT -i ogstun -j ACCEPT
  fi
  if ! _run iptables -C FORWARD -i ogstun -j ACCEPT 2>/dev/null; then
    _run iptables -I FORWARD -i ogstun -j ACCEPT
  fi
  if ! _run iptables -C FORWARD -o ogstun -j ACCEPT 2>/dev/null; then
    _run iptables -I FORWARD -o ogstun -j ACCEPT
  fi
else
  echo "[WARN] iptables não encontrado — configure NAT manualmente."
fi

echo
echo "ogstun: $(ip -br link show ogstun 2>/dev/null || echo 'ausente')"
AMF="${AMF_NGAP_ADDR:-127.0.1.100}"
if ss -lnu 2>/dev/null | grep -q "$AMF" || ss -ln 2>/dev/null | grep -q "${AMF_NGAP_PORT:-38412}"; then
  echo "AMF/NGAP parece escutar (porta ${AMF_NGAP_PORT:-38412})."
else
  echo "[INFO] Confira AMF: journalctl -u open5gs-amfd -n 30 --no-pager"
fi

# WebUI
if [[ "$START_WEBUI" -eq 1 ]] && unit_loaded open5gs-webui.service; then
  echo "Iniciando open5gs-webui.service ..."
  if _sc start open5gs-webui.service; then
    webui_ok=0
    for _ in $(seq 1 20); do
      if systemctl is-active --quiet open5gs-webui.service \
         && ss -ltn 2>/dev/null | grep -q ':9999'; then
        webui_ok=1
        break
      fi
      sleep 0.5
    done
    if [[ "$webui_ok" -eq 1 ]]; then
      echo "  WebUI: http://localhost:9999  (admin / 1423)"
    else
      echo "[WARN] webui não escuta :9999 — journalctl -u open5gs-webui -n 20"
    fi
  else
    echo "[WARN] falha ao iniciar open5gs-webui"
  fi
fi

echo
if [[ "$fail" -gt 0 || "$activating" -gt 0 ]]; then
  echo "[ERRO] Open5GS SA incompleto. Rode: bash scripts/network_jammer_status.sh"
  exit 1
fi
echo "Open5GS SA UP."
echo "  Status: bash scripts/network_jammer_status.sh"
echo "  gNB:    sudo bash scripts/gnb_run.sh"
