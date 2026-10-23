#!/usr/bin/env bash
# Manage the single-VM, private-LAN HTTP ground-station stack.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/stage9/compose.local.yaml"
PROJECT="pado-stage9-local"
FIREWALL_CHAIN="PADO_GS_LOCAL"
FIREWALL_MARK="pado-gs-local-http"
PORT=8080
PADO_LOCAL_BIND_IP="${PADO_LOCAL_BIND_IP:-192.168.45.128}"
PADO_OPERATOR_CIDR="${PADO_OPERATOR_CIDR:-192.168.45.1/32}"
export PADO_LOCAL_BIND_IP PADO_OPERATOR_CIDR

usage() {
  cat <<'EOF'
Usage: ./pado-gs.sh start|stop|status|cleanup [--yes]

  start            Build and start the local HTTP stack and its firewall rule.
  stop             Stop this stack and remove its firewall rule. Keep data.
  status           Show this stack's containers and firewall rule.
  cleanup          Remove this stack's containers, networks, local images and
                   named volumes (including accounts and saved drafts).
  cleanup --yes    Skip the interactive deletion confirmation.

Optional environment: PADO_LOCAL_BIND_IP (default 192.168.45.128),
PADO_OPERATOR_CIDR (default 192.168.45.1/32).
EOF
}

die() { printf 'pado-gs: %s\n' "$*" >&2; exit 1; }
note() { printf 'pado-gs: %s\n' "$*"; }
compose() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" "$@"; }
fw() {
  if (( EUID == 0 )); then
    iptables -w "$@"
  else
    sudo iptables -w "$@"
  fi
}

require_tools() {
  [[ "$(uname -s)" == Linux ]] || die 'Run this script on the Linux VM.'
  local command_name
  for command_name in docker iptables ip python3 curl; do
    command -v "$command_name" >/dev/null || die "Missing required command: $command_name"
  done
  if (( EUID != 0 )); then
    command -v sudo >/dev/null || die 'sudo is required to manage DOCKER-USER.'
  fi
  docker info --format '{{.ServerVersion}}' >/dev/null || die 'Docker daemon is unavailable.'
  docker compose version >/dev/null || die 'Docker Compose v2 is required.'
}

validate_lan() {
  python3 - "$PADO_LOCAL_BIND_IP" "$PADO_OPERATOR_CIDR" <<'PY' || die 'Invalid LAN bind address or operator CIDR.'
import ipaddress
import sys

try:
    bind = ipaddress.ip_address(sys.argv[1])
    operator = ipaddress.ip_network(sys.argv[2], strict=True)
    if bind.version != 4 or operator.version != 4:
        raise ValueError("IPv4 is required")
    if not bind.is_private or not operator.is_private:
        raise ValueError("addresses must be private LAN addresses")
except ValueError as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(1)
PY
  ip -4 -o addr show | python3 -c '
import ipaddress, sys
wanted = ipaddress.ip_address(sys.argv[1])
assigned = [ipaddress.ip_interface(line.split()[3]).ip for line in sys.stdin if len(line.split()) >= 4]
raise SystemExit(0 if wanted in assigned else 1)
' "$PADO_LOCAL_BIND_IP" || die "$PADO_LOCAL_BIND_IP is not assigned to this VM. Set PADO_LOCAL_BIND_IP to its current private IP."
}

firewall_available() {
  fw -S DOCKER-USER >/dev/null 2>&1 || die 'Docker DOCKER-USER chain is unavailable; refusing to publish port 8080 without its access rule.'
}

firewall_jump_exists() {
  fw -C DOCKER-USER -m comment --comment "$FIREWALL_MARK" -j "$FIREWALL_CHAIN" >/dev/null 2>&1
}

firewall_rules_present() {
  firewall_jump_exists &&
    fw -C "$FIREWALL_CHAIN" -m comment --comment "$FIREWALL_MARK" \
      -s "$PADO_OPERATOR_CIDR" -m conntrack --ctorigdst "$PADO_LOCAL_BIND_IP" \
      --ctorigdstport "$PORT" -j RETURN >/dev/null 2>&1 &&
    fw -C "$FIREWALL_CHAIN" -m conntrack --ctorigdst "$PADO_LOCAL_BIND_IP" \
      --ctorigdstport "$PORT" -j DROP >/dev/null 2>&1
}

firewall_chain_owned() {
  local rules
  rules="$(fw -S "$FIREWALL_CHAIN" 2>/dev/null)" || return 1
  [[ "$rules" == "-N $FIREWALL_CHAIN" || "$rules" == *"$FIREWALL_MARK"* ]]
}

install_firewall() {
  firewall_available
  if fw -S "$FIREWALL_CHAIN" >/dev/null 2>&1; then
    if firewall_jump_exists; then
      fw -D DOCKER-USER -m comment --comment "$FIREWALL_MARK" -j "$FIREWALL_CHAIN"
    else
      # A prior interrupted setup may have left an empty or marked chain.
      firewall_chain_owned || die "$FIREWALL_CHAIN exists but is not owned by this launcher."
    fi
    fw -F "$FIREWALL_CHAIN"
  else
    fw -N "$FIREWALL_CHAIN"
  fi
  # Docker publishes ports through FORWARD; UFW INPUT alone cannot restrict
  # them. Match the original host destination after Docker's DNAT.
  fw -A "$FIREWALL_CHAIN" -m comment --comment "$FIREWALL_MARK" \
    -s "$PADO_OPERATOR_CIDR" -m conntrack \
    --ctorigdst "$PADO_LOCAL_BIND_IP" --ctorigdstport "$PORT" -j RETURN
  fw -A "$FIREWALL_CHAIN" -m conntrack \
    --ctorigdst "$PADO_LOCAL_BIND_IP" --ctorigdstport "$PORT" -j DROP
  fw -A "$FIREWALL_CHAIN" -j RETURN
  fw -I DOCKER-USER 1 -m comment --comment "$FIREWALL_MARK" -j "$FIREWALL_CHAIN"
  firewall_rules_present || die 'Failed to verify the Docker firewall rule.'
}

remove_firewall() {
  # Never flush DOCKER-USER or modify any unrelated rule.
  if ! fw -S DOCKER-USER >/dev/null 2>&1; then
    note 'DOCKER-USER is unavailable; no host firewall rule to remove.'
    return
  fi
  if firewall_jump_exists; then
    fw -D DOCKER-USER -m comment --comment "$FIREWALL_MARK" -j "$FIREWALL_CHAIN"
    fw -F "$FIREWALL_CHAIN"
    fw -X "$FIREWALL_CHAIN"
  elif fw -S "$FIREWALL_CHAIN" >/dev/null 2>&1; then
    if firewall_chain_owned; then
      fw -F "$FIREWALL_CHAIN"
      fw -X "$FIREWALL_CHAIN"
    else
      die "$FIREWALL_CHAIN exists without our jump; leaving it untouched."
    fi
  fi
}

ensure_account() {
  local state
  state="$(compose run --rm --no-deps portal python3 -m services.webapp.main \
    account-status --db /state/accounts.db)" || die 'Could not inspect portal accounts.'
  state="${state##*$'\n'}"
  state="${state%$'\r'}"
  case "$state" in
    configured) return ;;
    empty)
      [[ -t 0 && -t 1 ]] || die "No portal account exists. Run './pado-gs.sh start' from an interactive VM terminal to create one."
      note 'Create the first portal account (planner01). Password input is hidden.'
      compose run --rm --no-deps portal python3 -m services.webapp.main \
        add-user --db /state/accounts.db --username planner01 --role planner
      ;;
    *) die "Unexpected account status: $state" ;;
  esac
}

wait_for_portal() {
  local attempt
  for attempt in {1..30}; do
    if curl --noproxy '*' --fail --silent --show-error --max-time 2 \
      --output /dev/null \
      "http://$PADO_LOCAL_BIND_IP:$PORT/login" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_services() {
  local attempt running service ready
  for attempt in {1..30}; do
    running="$(compose ps --status running --services)" || return 1
    running=$'\n'"$running"$'\n'
    ready=true
    for service in portal ros-api mps-api ros-core-read mps-core-read fds-iss-demo; do
      if [[ "$running" != *$'\n'"$service"$'\n'* ]]; then
        ready=false
        break
      fi
    done
    [[ "$ready" == true ]] && return 0
    sleep 1
  done
  return 1
}

rollback_start() {
  if ! compose stop; then
    die 'Startup failed and containers could not be stopped. Firewall rule retained.'
  fi
  remove_firewall
  die 'Startup failed; containers stopped and firewall rule removed.'
}

start_stack() {
  require_tools
  # An earlier run may still have its port published even if Docker's firewall
  # was reset. Quiesce it before checking or changing access rules.
  compose stop
  validate_lan
  firewall_available
  compose config --quiet
  note 'Building local services...'
  compose build
  ensure_account
  install_firewall
  if ! compose up -d; then
    rollback_start
  fi
  if ! wait_for_services; then
    rollback_start
  fi
  if ! wait_for_portal; then
    rollback_start
  fi
  if ! firewall_rules_present; then
    rollback_start
  fi
  note "Open http://$PADO_LOCAL_BIND_IP:$PORT/login from the operator PC ($PADO_OPERATOR_CIDR)."
  compose ps
}

stop_stack() {
  require_tools
  compose stop
  remove_firewall
  note 'Stopped. Named volumes and saved data remain.'
}

cleanup_stack() {
  local confirmed="${1:-}"
  [[ -z "$confirmed" || "$confirmed" == --yes ]] || { usage; exit 2; }
  require_tools
  if [[ "$confirmed" != --yes ]]; then
    [[ -t 0 ]] || die 'cleanup needs interactive confirmation or --yes.'
    printf 'Delete PADO Stage 9 containers, networks, local images and all named volumes (accounts/drafts)? Type DELETE: '
    local answer
    read -r answer
    [[ "$answer" == DELETE ]] || die 'Cleanup cancelled.'
  fi
  compose down --volumes --rmi local
  remove_firewall
  note 'Removed this Compose project and its Docker data. Repository files remain.'
}

case "${1:-}" in
  start) [[ "$#" -eq 1 ]] || { usage; exit 2; }; start_stack ;;
  stop) [[ "$#" -eq 1 ]] || { usage; exit 2; }; stop_stack ;;
  status)
    [[ "$#" -eq 1 ]] || { usage; exit 2; }
    require_tools
    compose ps
    if firewall_rules_present; then note 'Operator access rule is verified.'
    else note 'Operator access rule is absent or differs from the requested addresses.'; fi
    ;;
  cleanup) [[ "$#" -le 2 ]] || { usage; exit 2; }; cleanup_stack "${2:-}" ;;
  *) usage; exit 2 ;;
esac
