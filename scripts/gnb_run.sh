#!/usr/bin/env bash
# Sobe o srsRAN gNB (X310) na Tower02.
# Automatic profile fallback is disabled by default; explicit --allow-auto-fallback changes the profile.
# DEFAULT (lab_addrs.env / GNB_CONFIG): 100 MHz · 1T1R · 3750 MHz
#   configs/gnb_n78_100mhz_1t1r_x310.yml
#
# Uso:
#   sudo bash scripts/gnb_run.sh
#   sudo bash scripts/gnb_run.sh --force
#   sudo bash scripts/gnb_run.sh configs/gnb_n78_40mhz_2t2r_x310.yml
#   sudo env FORCE_GNB=1 bash scripts/gnb_run.sh   # FORCE_GNB antes de sudo NÃO funciona
#
# Pré: sudo bash scripts/tune_gnb_host_net.sh && sudo bash scripts/open5gs_up.sh
#
# Jumbo: se UHD reportar frame < 8000, perfil 100 MHz aborta ou (default) cai para 40 MHz.
# --force / FORCE_GNB=1: ignora preflight (pode OpTimeout).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$ROOT/configs/lab_addrs.env" ]] && source "$ROOT/configs/lab_addrs.env"

FORCE_GNB="${FORCE_GNB:-0}"
CFG_ARG=""
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force|-f) FORCE_GNB=1; shift ;;
    --no-auto-fallback) NO_AUTO_FALLBACK=1; shift ;;
    --allow-auto-fallback) NO_AUTO_FALLBACK=0; shift ;;
    -h|--help)
      sed -n '1,18p' "$0"
      exit 0
      ;;
    -*)
      echo "Arg desconhecido: $1 (use --force)" >&2
      exit 1
      ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
NO_AUTO_FALLBACK="${NO_AUTO_FALLBACK:-1}"

if [[ ${#POSITIONAL[@]} -gt 0 ]]; then
  CFG_ARG="${POSITIONAL[0]}"
fi

CFG="${CFG_ARG:-$ROOT/${GNB_CONFIG:-configs/gnb_n78_100mhz_1t1r_x310.yml}}"
GNB_BIN="${GNB_BIN:-gnb}"
ADDR="${GNB_USRP_ADDR:-192.168.40.3}"
IFACE="${GNB_IFACE:-enp2s0}"
LOG_DIR="$ROOT/data/logs"
LOG_FILE="$LOG_DIR/gnb.log"
FALLBACK_40="$ROOT/configs/gnb_n78_40mhz_2t2r_x310.yml"

resolve_cfg() {
  local c="$1"
  if [[ -f "$c" ]]; then
    echo "$c"
  elif [[ -f "$ROOT/$c" ]]; then
    echo "$ROOT/$c"
  else
    return 1
  fi
}

if ! CFG="$(resolve_cfg "$CFG")"; then
  echo "Config não encontrada: ${CFG_ARG:-$CFG}" >&2
  echo "Disponíveis:" >&2
  ls -1 "$ROOT"/configs/gnb_n78_*.yml 2>/dev/null | sed 's|.*/|  |' >&2 || true
  exit 1
fi

# Aliases de nomes antigos (pré-renomeação 2x2 → 2t2r)
case "$(basename "$CFG")" in
  gnb_n78_100mhz_x310.yml)
    CFG="$ROOT/configs/gnb_n78_100mhz_2t2r_x310.yml"
    echo "[INFO] Nome antigo → $(basename "$CFG")"
    ;;
  gnb_n78_40mhz_2x2_x310.yml)
    CFG="$FALLBACK_40"
    echo "[INFO] Nome antigo → $(basename "$CFG")"
    ;;
esac

if ! command -v "$GNB_BIN" >/dev/null 2>&1; then
  echo "Binário gnb não encontrado no PATH ($GNB_BIN)." >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

if [[ -e /tmp/gnb.log && ! -w /tmp/gnb.log ]]; then
  echo "[fix] /tmp/gnb.log sem escrita (provável dono root). Removendo..."
  if [[ "${EUID}" -eq 0 ]]; then
    rm -f /tmp/gnb.log
  else
    sudo rm -f /tmp/gnb.log || true
  fi
fi

HOST_MTU="$(cat /sys/class/net/"$IFACE"/mtu 2>/dev/null || echo 0)"
IFACE_STATE="$(cat /sys/class/net/"$IFACE"/operstate 2>/dev/null || echo unknown)"
echo "gNB → config=$CFG"
echo "     USRP=$ADDR  iface=$IFACE state=$IFACE_STATE host_mtu=$HOST_MTU"
echo "     log=$LOG_FILE  force=$FORCE_GNB"
echo

if [[ "$IFACE_STATE" != "up" && "$IFACE_STATE" != "unknown" ]]; then
  echo "[WARN] $IFACE operstate=$IFACE_STATE — tentando ip link set $IFACE up"
  if [[ "${EUID}" -eq 0 ]]; then
    ip link set "$IFACE" up 2>/dev/null || true
  else
    sudo ip link set "$IFACE" up 2>/dev/null || true
  fi
  IFACE_STATE="$(cat /sys/class/net/"$IFACE"/operstate 2>/dev/null || echo unknown)"
fi

# Preferir UHD do gNB (/usr/local, libuhd 4.9) sob o do sistema (/usr/bin 4.6)
UHD_FIND=""
UHD_PROBE=""
for c in /usr/local/bin/uhd_find_devices /usr/bin/uhd_find_devices; do
  [[ -z "$UHD_FIND" && -x "$c" ]] && UHD_FIND="$c"
done
for c in /usr/local/bin/uhd_usrp_probe /usr/bin/uhd_usrp_probe; do
  [[ -z "$UHD_PROBE" && -x "$c" ]] && UHD_PROBE="$c"
done
if [[ -z "$UHD_FIND" ]]; then
  UHD_FIND="$(command -v uhd_find_devices 2>/dev/null || true)"
fi
if [[ -z "$UHD_PROBE" ]]; then
  UHD_PROBE="$(command -v uhd_usrp_probe 2>/dev/null || true)"
fi
UHD_PROBE="${UHD_PROBE:-uhd_usrp_probe}"

REACH_OK=0
if ping -c1 -W2 -I "$IFACE" "$ADDR" >/dev/null 2>&1 \
   || ping -c1 -W2 "$ADDR" >/dev/null 2>&1; then
  REACH_OK=1
  echo "Ping OK → $ADDR"
else
  echo "[WARN] Ping falhou → $ADDR (iface=$IFACE)"
  if [[ -n "$UHD_FIND" ]]; then
    # --args=addr=... ; grep -F (não -E: '.' no IP viraria wildcard)
    UHD_OUT="$("$UHD_FIND" --args="addr=${ADDR}" 2>&1 || true)"
    if echo "$UHD_OUT" | grep -qF "$ADDR" && echo "$UHD_OUT" | grep -qiE 'x300|X300|type: x300'; then
      REACH_OK=1
      echo "[INFO] Ping falhou, mas uhd_find_devices vê $ADDR — seguindo."
    else
      echo "[WARN] uhd_find_devices --args=addr=$ADDR: nenhum X300"
    fi
  fi
fi

if [[ "$REACH_OK" -ne 1 ]]; then
  echo
  echo "[ERRO] USRP $ADDR inalcançável (ping e UHD)."
  echo "       Host/link OK não basta: o X310 precisa responder ICMP/UHD."
  echo "       Checklist:"
  echo "         1) LEDs/SFP#1 do X310 + fibra → DGS-1510 porta 27"
  echo "         2) Power-cycle do X310 (30 s desligado)"
  echo "         3) ping -c2 -I $IFACE $ADDR"
  echo "         4) $UHD_FIND --args=addr=$ADDR"
  echo "         5) sudo bash scripts/tune_gnb_host_net.sh"
  if [[ "$FORCE_GNB" == "1" ]]; then
    echo "[WARN] --force: ignorando reachability (gnb provavelmente falha igual)"
  else
    exit 3
  fi
fi
echo

FRAME_MSG="$(
  python3 - <<PY 2>/dev/null || true
import re, subprocess, sys
addr = "${ADDR}"
probe = "${UHD_PROBE}"
try:
    out = subprocess.check_output(
        [probe, f"--args=addr={addr},recv_frame_size=8000,send_frame_size=8000"],
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
except Exception as e:
    err = str(e)
    print(f"probe_error:{err}")
    if "No devices found" in err or "exit status 255" in err or "LookupError" in err:
        print("PROBE_NO_DEVICE")
    sys.exit(0)
if "No devices found" in out:
    print("PROBE_NO_DEVICE")
    sys.exit(0)
m = re.search(r"Maximum frame size:\s*(\d+)", out)
print(m.group(1) if m else "unknown")
PY
)"

if [[ "$FRAME_MSG" == *PROBE_NO_DEVICE* ]]; then
  echo
  echo "[ERRO] uhd_usrp_probe não abre $ADDR (No devices found)."
  echo "       Mesma falha que o gNB teria — abortando."
  echo "       Power-cycle do X310 + conferir SFP/fibra/switch porta 27."
  if [[ "$FORCE_GNB" == "1" ]]; then
    echo "[WARN] --force: seguindo mesmo assim"
  else
    exit 3
  fi
elif [[ "$FRAME_MSG" =~ ^[0-9]+$ ]]; then
  echo "UHD Maximum frame size: $FRAME_MSG bytes"
  if [[ "$FRAME_MSG" -lt 8000 ]]; then
    echo
    echo "[WARN] Frame size $FRAME_MSG < 8000 (host MTU=$HOST_MTU)."
    echo "       Jumbo incompleto no caminho (switch DGS-1510 portas 25/27?)."
    echo "       100 MHz @ 184.32 MS/s costuma dar RfnocError: OpTimeout sem jumbo."
    echo "       Checklist: sudo bash scripts/tune_gnb_host_net.sh + jumbo no switch"
    echo

    if [[ "$FORCE_GNB" == "1" ]]; then
      echo "[WARN] --force: mantendo $(basename "$CFG") apesar do frame $FRAME_MSG"
    elif [[ "$CFG" == *100mhz* && "$NO_AUTO_FALLBACK" != "1" && -f "$FALLBACK_40" ]]; then
      echo "[AUTO] Trocando para fallback 40 MHz 2T2R (adequado sem jumbo):"
      echo "       $(basename "$FALLBACK_40")"
      CFG="$FALLBACK_40"
    elif [[ "$CFG" == *100mhz* ]]; then
      echo "Abortando 100 MHz. Opções:"
      echo "  sudo bash scripts/gnb_run.sh configs/gnb_n78_40mhz_2t2r_x310.yml"
      echo "  sudo bash scripts/gnb_run.sh --force    # força 100 MHz (arriscado)"
      exit 2
    else
      echo "[INFO] Perfil não-100 MHz — seguindo com frame $FRAME_MSG"
    fi
  fi
else
  echo "[WARN] Não foi possível ler frame size UHD ($FRAME_MSG)."
  if [[ "$FORCE_GNB" == "1" ]]; then
    echo "[WARN] --force: seguindo sem frame size"
  else
    echo "       Sem probe OK o gNB costuma falhar com 'No devices found'."
    echo "       Use --force só se souber que o rádio responde."
    exit 3
  fi
fi

echo
echo "Ctrl+C para parar. Config efetiva: $CFG"
echo

cd "$ROOT"
CMD=(
  "$GNB_BIN" -c "$CFG"
  --log.filename="$LOG_FILE"
  --metrics.autostart_stdout_metrics=true
)

if [[ -n "${GNB_CPUSET:-}" ]]; then
  if command -v taskset >/dev/null 2>&1; then
    echo "taskset -c $GNB_CPUSET"
    CMD=(taskset -c "$GNB_CPUSET" "${CMD[@]}")
  else
    echo "[WARN] taskset não encontrado — GNB_CPUSET ignorado"
  fi
fi

if [[ "${EUID}" -ne 0 ]]; then
  # -E preserva FORCE_GNB se reentrarmos; aqui já somos o launcher
  exec sudo -E "${CMD[@]}"
else
  exec "${CMD[@]}"
fi
