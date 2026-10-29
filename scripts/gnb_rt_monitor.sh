#!/usr/bin/env bash
# Procura falhas de tempo real no log do gNB e (opcional) no journal.
#
# Uso:
#   bash scripts/gnb_rt_monitor.sh
#   bash scripts/gnb_rt_monitor.sh --log data/logs/gnb.log
#   bash scripts/gnb_rt_monitor.sh --follow
#   sudo bash scripts/gnb_rt_monitor.sh --journal   # uhd/network/drop/watchdog

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/data/logs/gnb.log"
FOLLOW=0
JOURNAL=0

PATTERN='late|underflow|overflow|overrun|underrun|deadline|real-time failure|OpTimeout|RfnocError'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --log) LOG="$2"; shift 2 ;;
    --follow|-f) FOLLOW=1; shift ;;
    --journal|-j) JOURNAL=1; shift ;;
    -h|--help)
      sed -n '1,12p' "$0"
      exit 0
      ;;
    *) echo "Arg desconhecido: $1" >&2; exit 1 ;;
  esac
done

scan_log() {
  local f="$1"
  if [[ ! -f "$f" ]]; then
    echo "[WARN] Log não encontrado: $f"
    return 1
  fi
  echo "=== gNB log: $f ==="
  if grep -i -E "$PATTERN" "$f"; then
    echo
    echo "[!] Encontradas linhas com possível falha RT / UHD acima."
    return 2
  fi
  echo "(nenhuma ocorrência de late/underflow/overflow/OpTimeout)"
  return 0
}

if [[ "$FOLLOW" -eq 1 && -f "$LOG" ]]; then
  echo "=== tail -f $LOG (Ctrl+C para sair) ==="
  tail -f "$LOG" | grep --line-buffered -i -E "$PATTERN" || true
  exit 0
fi

rc=0
scan_log "$LOG" || rc=$?

if [[ "$JOURNAL" -eq 1 ]]; then
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "[WARN] --journal requer sudo" >&2
  else
    echo
    echo "=== journalctl (uhd|network|drop|watchdog|soft lockup) ==="
    journalctl -k --since "10 min ago" --no-pager 2>/dev/null \
      | grep -i -E 'uhd|network|drop|watchdog|soft lockup' || true
  fi
fi

exit "$rc"
