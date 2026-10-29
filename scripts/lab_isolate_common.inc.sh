# shellcheck shell=bash
# Funções compartilhadas — isolamento de host para experimentos CERISE.
# Incluir com: source "$(dirname "$0")/lab_isolate_common.inc.sh"

lab_isolate_require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Execute com sudo: sudo bash $0 $*" >&2
    exit 1
  fi
}

lab_isolate_state_dir() {
  echo "/var/run/cerise-gnb-isolate"
}

lab_isolate_state_file() {
  echo "$(lab_isolate_state_dir)/state.env"
}

lab_isolate_ensure_state_dir() {
  mkdir -p "$(lab_isolate_state_dir)"
  chmod 700 "$(lab_isolate_state_dir)"
}

lab_isolate_is_active() {
  [[ -f "$(lab_isolate_state_file)" ]]
}

lab_isolate_record() {
  local key="$1" val="$2"
  lab_isolate_ensure_state_dir
  printf '%s=%q\n' "$key" "$val" >>"$(lab_isolate_state_file)"
}

lab_isolate_stop_unit() {
  local u="$1"
  if systemctl is-active --quiet "$u" 2>/dev/null; then
    systemctl stop "$u" 2>/dev/null || true
    lab_isolate_record "stopped_unit" "$u"
    echo "  stop $u"
  fi
}

lab_isolate_stop_units() {
  local u
  for u in "$@"; do
    lab_isolate_stop_unit "$u"
  done
}

lab_isolate_mask_unit() {
  local u="$1"
  if systemctl is-enabled --quiet "$u" 2>/dev/null; then
    systemctl mask "$u" 2>/dev/null || true
    lab_isolate_record "masked_unit" "$u"
    echo "  mask $u"
  fi
}

lab_isolate_set_performance_governor() {
  echo "[cpu] governor → performance"
  if command -v cpupower >/dev/null 2>&1; then
    cpupower frequency-set -g performance >/dev/null 2>&1 || true
  elif [[ -d /sys/devices/system/cpu ]]; then
    local g
    for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
      [[ -f "$g" ]] && echo performance >"$g" 2>/dev/null || true
    done
  fi
}

lab_isolate_disable_kms_polling() {
  local p="/sys/module/drm_kms_helper/parameters/poll"
  if [[ -f "$p" ]]; then
    local prev
    prev="$(cat "$p" 2>/dev/null || echo "?")"
    lab_isolate_record "kms_poll" "$prev"
    echo N >"$p" 2>/dev/null || true
    echo "[gpu] DRM KMS polling → N (era $prev)"
  fi
}

lab_isolate_apply_srsran_performance() {
  # Equivalente não-interativo a srsRAN_Project/scripts/srsran_performance
  lab_isolate_set_performance_governor
  lab_isolate_disable_kms_polling
}

lab_isolate_tune_vm() {
  echo "[vm] swappiness=1, THP=never (sessão)"
  local sw
  sw="$(sysctl -n vm.swappiness 2>/dev/null || echo 60)"
  lab_isolate_record "vm_swappiness" "$sw"
  sysctl -w vm.swappiness=1 >/dev/null 2>&1 || true

  local thp="/sys/kernel/mm/transparent_hugepage/enabled"
  if [[ -f "$thp" ]]; then
    local prev
    prev="$(cat "$thp" 2>/dev/null || echo "?")"
    lab_isolate_record "thp_enabled" "$prev"
    echo never >"$thp" 2>/dev/null || true
  fi
}

lab_isolate_disable_swap() {
  if swapon --show 2>/dev/null | grep -q .; then
    lab_isolate_record "swap_was_on" "1"
    echo "[vm] swapoff -a"
    swapoff -a 2>/dev/null || true
  else
    lab_isolate_record "swap_was_on" "0"
  fi
}

lab_isolate_stop_irqbalance() {
  lab_isolate_stop_unit irqbalance.service
}

lab_isolate_mount_debugfs() {
  if [[ ! -d /sys/kernel/debug/sched ]]; then
    mount -t debugfs none /sys/kernel/debug 2>/dev/null || true
  fi
}

lab_isolate_read_preempt_mode() {
  lab_isolate_mount_debugfs
  if [[ -f /sys/kernel/debug/sched/preempt ]]; then
    cat /sys/kernel/debug/sched/preempt
  else
    echo "unavailable (debugfs ou kernel sem sched/preempt)"
  fi
}

lab_isolate_competing_units() {
  # Serviços que competem por CPU/IO — NÃO incluir open5gs-* nem mongod.
  # NÃO incluir ModemManager: UE Quectel (wwan) depende dele para dados.
  cat <<'EOF'
docker.service
docker.socket
containerd.service
cups.service
cups-browsed.service
avahi-daemon.service
bluetooth.service
snap.canonical-livepatch.service
fwupd.service
packagekit.service
unattended-upgrades.service
boltd.service
power-profiles-daemon.service
EOF
}

lab_isolate_stop_competing() {
  echo "[services] parando serviços concorrentes (Open5GS/mongod permanecem)"
  local u
  while IFS= read -r u; do
    [[ -n "$u" ]] && lab_isolate_stop_unit "$u"
  done < <(lab_isolate_competing_units)

  # Timers de atualização automática
  lab_isolate_stop_unit apt-daily.timer
  lab_isolate_stop_unit apt-daily-upgrade.timer
  lab_isolate_stop_unit fwupd-refresh.timer
}

lab_isolate_restore_units_from_state() {
  local state="$1"
  [[ -f "$state" ]] || return 0

  local masked=()
  local stopped=()
  local line key val
  while IFS= read -r line; do
    [[ "$line" =~ ^([^=]+)=(.+)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    val="${BASH_REMATCH[2]}"
    # shellcheck disable=SC2086
    eval "val=$val"
    case "$key" in
      masked_unit) masked+=("$val") ;;
      stopped_unit) stopped+=("$val") ;;
    esac
  done <"$state"

  local u
  for u in "${masked[@]}"; do
    systemctl unmask "$u" 2>/dev/null || true
    echo "  unmask $u"
  done
  for u in "${stopped[@]}"; do
    systemctl start "$u" 2>/dev/null || true
    echo "  start $u"
  done
}

lab_isolate_restore_vm_from_state() {
  local state="$1"
  [[ -f "$state" ]] || return 0

  local sw thp swap_on kms
  sw="$(grep -m1 '^vm_swappiness=' "$state" 2>/dev/null | cut -d= -f2- || true)"
  thp="$(grep -m1 '^thp_enabled=' "$state" 2>/dev/null | cut -d= -f2- || true)"
  swap_on="$(grep -m1 '^swap_was_on=' "$state" 2>/dev/null | cut -d= -f2- || true)"
  kms="$(grep -m1 '^kms_poll=' "$state" 2>/dev/null | cut -d= -f2- || true)"

  [[ -n "$sw" && "$sw" =~ ^[0-9]+$ ]] && sysctl -w "vm.swappiness=$sw" >/dev/null 2>&1 || true
  [[ -n "$thp" && -f /sys/kernel/mm/transparent_hugepage/enabled ]] && echo "$thp" >/sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
  [[ "$swap_on" == "1" ]] && swapon -a 2>/dev/null || true
  [[ -n "$kms" && -f /sys/module/drm_kms_helper/parameters/poll ]] && echo "$kms" >/sys/module/drm_kms_helper/parameters/poll 2>/dev/null || true
}
