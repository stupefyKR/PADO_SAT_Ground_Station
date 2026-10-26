#!/usr/bin/env bash
# Manage the single-VM, private-LAN HTTP ground-station stack.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/stage9/compose.local.yaml"
PROJECT="pado-stage9-local"
FIREWALL_CHAIN="PADO_GS_LOCAL"
FIREWALL_MARK="pado-gs-local-http"
HOST_FIREWALL_MARK="pado-gs-local-host"
ENTRY_BRIDGE="br-pado-local"
PORT=8080
PADO_LOCAL_BIND_IP="${PADO_LOCAL_BIND_IP:-192.168.45.128}"
PADO_OPERATOR_CIDR="${PADO_OPERATOR_CIDR:-192.168.45.1/32}"
export PADO_LOCAL_BIND_IP PADO_OPERATOR_CIDR

usage() {
  cat <<'EOF'
Usage: ./pado-gs.sh start|stop|status|cleanup [--yes]

  start            Recreate and start the local HTTP stack with its DGS-only
                   Docker network and firewall rules. Named data is retained.
  stop             Stop this stack and remove its firewall rule. Keep data.
  status           Show this stack's containers and firewall rule.
  cleanup          Remove this stack's containers, networks, local images and
                   named volumes (including accounts and saved drafts).
  cleanup --yes    Skip the interactive deletion confirmation.

Optional environment: PADO_LOCAL_BIND_IP (default 192.168.45.128),
PADO_OPERATOR_CIDR (default 192.168.45.1/32).
Start requires Docker Engine 28.0.0 or newer and Docker Compose v2.
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
  for command_name in docker iptables ip python3; do
    command -v "$command_name" >/dev/null || die "Missing required command: $command_name"
  done
  if (( EUID != 0 )); then
    command -v sudo >/dev/null || die 'sudo is required to manage DOCKER-USER.'
  fi
  docker info --format '{{.ServerVersion}}' >/dev/null || die 'Docker daemon is unavailable.'
  docker compose version >/dev/null || die 'Docker Compose v2 is required.'
}

require_patched_engine() {
  local engine_version
  engine_version="$(docker info --format '{{.ServerVersion}}')" || die 'Docker daemon is unavailable.'
  python3 - "$engine_version" <<'PY' || die 'Docker Engine 28.0.0 or newer (stable) is required for isolated internal bridges and patched internal DNS.'
import re
import sys

match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\+[A-Za-z0-9.-]+)?", sys.argv[1])
if not match or tuple(map(int, match.groups()[:3])) < (28, 0, 0):
    raise SystemExit(1)
PY
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
    lan_ranges = tuple(map(ipaddress.ip_network,
                           ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
    if not any(bind in lan for lan in lan_ranges) or not any(
            operator.subnet_of(lan) for lan in lan_ranges):
        raise ValueError("addresses must be RFC 1918 private LAN addresses")
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

firewall_rules_ordered() {
  local docker_rules input_rules owned_rules
  docker_rules="$(fw -S DOCKER-USER)" || return 1
  input_rules="$(fw -S INPUT)" || return 1
  owned_rules="$(fw -S "$FIREWALL_CHAIN")" || return 1
  PADO_DOCKER_RULES="$docker_rules" PADO_INPUT_RULES="$input_rules" \
    PADO_OWNED_RULES="$owned_rules" python3 - \
    "$FIREWALL_CHAIN" "$FIREWALL_MARK" "$HOST_FIREWALL_MARK" \
    "$ENTRY_BRIDGE" "$PADO_OPERATOR_CIDR" "$PADO_LOCAL_BIND_IP" "$PORT" <<'PY'
import os
import ipaddress
import shlex
import sys

chain, mark, host_mark, bridge, operator, bind, port = sys.argv[1:]

def rules(variable, name):
    return [shlex.split(line) for line in os.environ[variable].splitlines()
            if line.startswith(f"-A {name} ")]

def canonical(rule):
    allowed = {"-A", "-s", "-i", "-o", "-m", "--comment", "--ctdir",
               "--ctorigdst", "--ctorigdstport", "--ctstate", "-j"}
    atoms = []
    index = 0
    while index < len(rule):
        inverted = rule[index] == "!"
        if inverted:
            index += 1
        if index + 1 >= len(rule) or rule[index] not in allowed:
            raise SystemExit(1)
        flag, value = rule[index:index + 2]
        if flag == "-s":
            value = str(ipaddress.ip_network(value, strict=False))
        elif flag == "--ctstate":
            value = ",".join(sorted(value.split(",")))
        atoms.append((inverted, flag, value))
        index += 2
    return sorted(atoms)

docker = rules("PADO_DOCKER_RULES", "DOCKER-USER")
host = rules("PADO_INPUT_RULES", "INPUT")
owned = rules("PADO_OWNED_RULES", chain)
if not docker or canonical(docker[0]) != canonical(
        ["-A", "DOCKER-USER", "-m", "comment", "--comment", mark,
         "-j", chain]):
    raise SystemExit(1)
expected_host = ["-A", "INPUT", "-i", bridge, "-m", "conntrack", "!",
                 "--ctstate", "ESTABLISHED,RELATED", "-m", "comment",
                 "--comment", host_mark, "-j", "DROP"]
if not host or canonical(host[0]) != canonical(expected_host):
    raise SystemExit(1)
expected_owned = [
    ["-A", chain, "-m", "comment", "--comment", mark,
     "-s", operator, "-m", "conntrack", "--ctdir", "ORIGINAL",
     "--ctorigdst", bind, "--ctorigdstport", port, "-j", "RETURN"],
    ["-A", chain, "-m", "conntrack", "--ctdir", "ORIGINAL",
     "--ctorigdst", bind, "--ctorigdstport", port, "-j", "DROP"],
    ["-A", chain, "-o", bridge, "-j", "DROP"],
    ["-A", chain, "-i", bridge, "-m", "conntrack", "!",
     "--ctstate", "ESTABLISHED,RELATED", "-j", "DROP"],
    ["-A", chain, "-j", "RETURN"],
]
if [canonical(rule) for rule in owned] != [canonical(rule) for rule in expected_owned]:
    raise SystemExit(1)
PY
}

firewall_rules_present() {
  firewall_jump_exists &&
    fw -C "$FIREWALL_CHAIN" -m comment --comment "$FIREWALL_MARK" \
      -s "$PADO_OPERATOR_CIDR" -m conntrack --ctdir ORIGINAL \
      --ctorigdst "$PADO_LOCAL_BIND_IP" \
      --ctorigdstport "$PORT" -j RETURN >/dev/null 2>&1 &&
    fw -C "$FIREWALL_CHAIN" -m conntrack --ctdir ORIGINAL \
      --ctorigdst "$PADO_LOCAL_BIND_IP" \
      --ctorigdstport "$PORT" -j DROP >/dev/null 2>&1 &&
    fw -C "$FIREWALL_CHAIN" -o "$ENTRY_BRIDGE" -j DROP >/dev/null 2>&1 &&
    fw -C "$FIREWALL_CHAIN" -i "$ENTRY_BRIDGE" -m conntrack ! \
      --ctstate ESTABLISHED,RELATED -j DROP >/dev/null 2>&1 &&
    fw -C INPUT -i "$ENTRY_BRIDGE" -m conntrack ! \
      --ctstate ESTABLISHED,RELATED -m comment --comment "$HOST_FIREWALL_MARK" \
      -j DROP >/dev/null 2>&1 &&
    firewall_rules_ordered
}

firewall_chain_owned() {
  local rules
  rules="$(fw -S "$FIREWALL_CHAIN" 2>/dev/null)" || return 1
  [[ "$rules" == "-N $FIREWALL_CHAIN" || "$rules" == *"$FIREWALL_MARK"* ]]
}

install_firewall() {
  firewall_available
  if fw -S "$FIREWALL_CHAIN" >/dev/null 2>&1 && ! firewall_chain_owned; then
    die "$FIREWALL_CHAIN exists but is not owned by this launcher."
  fi
  if fw -C INPUT -i "$ENTRY_BRIDGE" -m conntrack ! \
    --ctstate ESTABLISHED,RELATED -m comment --comment "$HOST_FIREWALL_MARK" \
    -j DROP >/dev/null 2>&1; then
    fw -D INPUT -i "$ENTRY_BRIDGE" -m conntrack ! \
      --ctstate ESTABLISHED,RELATED -m comment --comment "$HOST_FIREWALL_MARK" \
      -j DROP
  fi
  fw -I INPUT 1 -i "$ENTRY_BRIDGE" -m conntrack ! \
    --ctstate ESTABLISHED,RELATED -m comment --comment "$HOST_FIREWALL_MARK" \
    -j DROP
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
  # them. Match the original host destination after Docker's DNAT, but only
  # in the request direction; the same original tuple exists on replies.
  fw -A "$FIREWALL_CHAIN" -m comment --comment "$FIREWALL_MARK" \
    -s "$PADO_OPERATOR_CIDR" -m conntrack --ctdir ORIGINAL \
    --ctorigdst "$PADO_LOCAL_BIND_IP" --ctorigdstport "$PORT" -j RETURN
  fw -A "$FIREWALL_CHAIN" -m conntrack --ctdir ORIGINAL \
    --ctorigdst "$PADO_LOCAL_BIND_IP" --ctorigdstport "$PORT" -j DROP
  # Refuse direct routing to the container IP, even if daemon-level Docker
  # direct routing is enabled or a host adds a route to this bridge.
  fw -A "$FIREWALL_CHAIN" -o "$ENTRY_BRIDGE" -j DROP
  # The sole externally connected container is DGS. It may answer operator
  # requests, but this development stack has no authorised outbound peer.
  fw -A "$FIREWALL_CHAIN" -i "$ENTRY_BRIDGE" -m conntrack ! \
    --ctstate ESTABLISHED,RELATED -j DROP
  fw -A "$FIREWALL_CHAIN" -j RETURN
  fw -I DOCKER-USER 1 -m comment --comment "$FIREWALL_MARK" -j "$FIREWALL_CHAIN"
  firewall_rules_present || die 'Failed to verify the Docker firewall rule.'
}

remove_firewall() {
  # Never flush DOCKER-USER or modify any unrelated rule.
  if fw -C INPUT -i "$ENTRY_BRIDGE" -m conntrack ! \
    --ctstate ESTABLISHED,RELATED -m comment --comment "$HOST_FIREWALL_MARK" \
    -j DROP >/dev/null 2>&1; then
    fw -D INPUT -i "$ENTRY_BRIDGE" -m conntrack ! \
      --ctstate ESTABLISHED,RELATED -m comment --comment "$HOST_FIREWALL_MARK" \
      -j DROP
  fi
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
    # A host-origin probe is not an operator-PC connection and must not pass
    # the gateway's peer CIDR check. Verify the portal within its namespace;
    # the external peer path is exercised by the Linux boundary integration.
    if compose exec -T portal python3 -c \
      'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/login", timeout=2).close()' \
      >/dev/null 2>&1 && \
      compose exec -T dgs-ops-gateway python3 -c \
      'import socket; socket.create_connection(("portal", 8080), timeout=2).close()' \
      >/dev/null 2>&1; then
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
    for service in dgs-ops-gateway portal ros-api mps-api ros-core-read mps-core-read fds-synthetic-demo fds-iss-display sim-ground sim-satellite; do
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
  if ! compose down; then
    die 'Startup failed and containers or networks could not be removed. Firewall rule retained.'
  fi
  remove_firewall
  die 'Startup failed; containers and networks removed, data volumes kept, firewall rule removed.'
}

verify_runtime_networks() {
  local require_gateway="${1:-}"
  docker network inspect \
    "${PROJECT}_entry" "${PROJECT}_dgs_portal" \
    "${PROJECT}_portal_ros" "${PROJECT}_portal_mps" \
    "${PROJECT}_sim_tm" | \
    python3 "$ROOT_DIR/deploy/stage9/check_compose_policy.py" \
      --actual-networks --project "$PROJECT" --bridge "$ENTRY_BRIDGE" \
      --bind-ip "$PADO_LOCAL_BIND_IP" --port "$PORT" \
      ${require_gateway:+--require-gateway}
}

validate_compose_config() {
  compose config --format json | python3 "$ROOT_DIR/deploy/stage9/check_compose_policy.py" \
    --bind-ip "$PADO_LOCAL_BIND_IP" --port "$PORT" --bridge "$ENTRY_BRIDGE" \
    --operator-cidr "$PADO_OPERATOR_CIDR" --project "$PROJECT"
}

start_stack() {
  require_tools
  require_patched_engine
  # An earlier run may still have its port published even if Docker's firewall
  # was reset. Quiesce it before checking or changing access rules.
  compose stop
  # Recreate the bridge rather than trusting an older network with the same
  # Compose name and different gateway settings. Named data volumes remain.
  compose down
  validate_lan
  firewall_available
  validate_compose_config || \
    die 'Compose topology violates the DGS-only external communication policy.'
  note 'Building local services...'
  compose build
  ensure_account
  install_firewall
  if ! compose create --no-build; then
    rollback_start
  fi
  if ! verify_runtime_networks; then
    rollback_start
  fi
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
  if ! verify_runtime_networks --require-gateway; then
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
    policy_ok=true
    if firewall_rules_present; then note 'Operator access rule is verified.'
    else note 'Operator access rule is absent, reordered or differs from policy.'; policy_ok=false; fi
    if validate_compose_config && verify_runtime_networks --require-gateway; then
      note 'DGS-only Docker network topology is verified.'
    else
      note 'DGS-only Docker network topology is absent or differs from policy.'
      policy_ok=false
    fi
    [[ "$policy_ok" == true ]] || exit 1
    ;;
  cleanup) [[ "$#" -le 2 ]] || { usage; exit 2; }; cleanup_stack "${2:-}" ;;
  *) usage; exit 2 ;;
esac
