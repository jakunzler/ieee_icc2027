#!/usr/bin/env bash
# Captura Fig.1 sob isolamento: isolate → capture → fig1 → (opcional) restore.
#
# Defaults lidos de configs/default.yaml (fig1.capture_* , radio.fc_hz, fig1.notch_dc).
#
# Uso:
#   bash scripts/watchdog_fig1_session.sh
#   bash scripts/watchdog_fig1_session.sh --no-restore
#   bash scripts/watchdog_fig1_session.sh --gain 15 --seconds 20

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
CFG="${WATCHDOG_CONFIG:-$ROOT/configs/default.yaml}"

# Carrega defaults do YAML
eval "$(
  PYTHONPATH="$ROOT/src:${PYTHONPATH:-}" /usr/bin/python3 - <<PY
from ref_repro.config import load_config
c = load_config("$CFG")
f, r = c.get("fig1", {}), c.get("radio", {})
print(f'DEF_GAIN={float(f.get("capture_gain", 20))}')
print(f'DEF_SECONDS={float(f.get("capture_seconds", 20))}')
print(f'DEF_FS={float(f.get("capture_fs_hz", 200e6))}')
print(f'DEF_FC={float(r.get("fc_hz", 3.75e9))}')
print(f'DEF_NOTCH_DC={1 if f.get("notch_dc", True) else 0}')
PY
)"

GAIN="$DEF_GAIN"
SECONDS_CAP="$DEF_SECONDS"
FS="$DEF_FS"
FC="$DEF_FC"
ADDR=192.168.40.2
RESTORE=1
CASE=A
NOTCH_DC="$DEF_NOTCH_DC"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gain) GAIN="$2"; shift 2 ;;
    --seconds) SECONDS_CAP="$2"; shift 2 ;;
    --fs) FS="$2"; shift 2 ;;
    --fc) FC="$2"; shift 2 ;;
    --addr) ADDR="$2"; shift 2 ;;
    --case) CASE="$2"; shift 2 ;;
    --config) CFG="$2"; shift 2 ;;
    --notch-dc) NOTCH_DC=1; shift ;;
    --no-notch-dc) NOTCH_DC=0; shift ;;
    --no-restore) RESTORE=0; shift ;;
    -h|--help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
    *) echo "Arg desconhecido: $1"; exit 1 ;;
  esac
done

echo "=== Config: $CFG ==="
echo "    gain=$GAIN  seconds=$SECONDS_CAP  fs=$FS  fc=$FC  notch_dc=$NOTCH_DC"

echo "=== 1) Isolar host (sudo) ==="
sudo bash scripts/watchdog_isolate_on.sh --net

echo "=== 2) Captura IQ ==="
export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"
mkdir -p data/raw/fig1
/usr/bin/python3 -m ref_repro.capture.watchdog_capture \
  --config "$CFG" \
  --defaults-from fig1 \
  --case "$CASE" --seconds "$SECONDS_CAP" --fs "$FS" --fc "$FC" --gain "$GAIN" \
  --addr "$ADDR" --out data/raw/fig1 --purpose fig1 \
  --recv-buff-size 250000000 --num-recv-frames 2048 --write-chunk-mib 128

IQ="$(ls -1t data/raw/fig1/caso${CASE}_*.c64 | head -1)"
echo "IQ: $IQ"

echo "=== 3) Fig. 1 (venv) ==="
# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="$ROOT/src:${PYTHONPATH:-}"
NOTCH_FLAG=(--notch-dc)
[[ "$NOTCH_DC" == "0" ]] && NOTCH_FLAG=(--no-notch-dc)
python scripts/fig1_plot_spectrogram.py \
  --config "$CFG" \
  --iq "$IQ" \
  --title "Fig. 1 (${SECONDS_CAP} s, 120 MHz span, gain=${GAIN})" \
  "${NOTCH_FLAG[@]}"

STAMP="$(basename "$IQ" | sed -E 's/.*_(20[0-9]{6}_[0-9]{6}).*/\1/')"
DUR_INT="$(printf '%.0f' "$SECONDS_CAP")"
OUT="data/figures/fig1/${STAMP}-fig1_${DUR_INT}s_120MHz.png"

echo "=== 4) Restore ==="
if [[ "$RESTORE" -eq 1 ]]; then
  sudo bash scripts/watchdog_isolate_off.sh
else
  echo "Isolate permanece ON (--no-restore). Rode depois: sudo bash scripts/watchdog_isolate_off.sh"
fi

echo
echo "[OK] $OUT"
ls -lh "$OUT" "${OUT}.json" 2>/dev/null || true
cat "${IQ}.json"
