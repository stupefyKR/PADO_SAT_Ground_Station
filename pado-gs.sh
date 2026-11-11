#!/usr/bin/env bash
# Manage the single-VM HTTP stack, published on loopback by default.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$ROOT_DIR/deploy/compose.local.yaml"
PROJECT="pado-stage9-local"
FIREWALL_CHAIN="PADO_GS_LOCAL"
FIREWALL_MARK="pado-gs-local-http"
HOST_FIREWALL_MARK="pado-gs-local-host"
ENTRY_BRIDGE="br-pado-local"
PORT=8080
PADO_LOCAL_BIND_IP="${PADO_LOCAL_BIND_IP:-127.0.0.1}"
if [[ "$PADO_LOCAL_BIND_IP" == 127.0.0.1 ]]; then
  PADO_OPERATOR_CIDR="${PADO_OPERATOR_CIDR:-127.0.0.1/32}"
else
  PADO_OPERATOR_CIDR="${PADO_OPERATOR_CIDR:-192.168.45.1/32}"
fi
export PADO_LOCAL_BIND_IP PADO_OPERATOR_CIDR
PADO_ENVIRONMENT="${PADO_ENVIRONMENT:-legacy}"
export PADO_ENVIRONMENT
BASE_COMPOSE_FILE="$COMPOSE_FILE"
CONFIG_DIR="$ROOT_DIR/config"
PROFILE_ARGS=()
case "$PADO_ENVIRONMENT" in
  legacy) ;;
  validation) ;;
  operations)
    PROJECT=pado-operations; PORT=8082; ENTRY_BRIDGE=br-pado-ops
    FIREWALL_CHAIN=PADO_GS_OPS; FIREWALL_MARK=pado-gs-ops-http; HOST_FIREWALL_MARK=pado-gs-ops-host
    CONFIG_DIR="$ROOT_DIR/config/operations"
    ;;
  *) printf 'Invalid PADO_ENVIRONMENT\n' >&2; exit 2 ;;
esac

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

Optional environment: PADO_LOCAL_BIND_IP (default 127.0.0.1),
PADO_OPERATOR_CIDR (default 127.0.0.1/32 for loopback;
192.168.45.1/32 when a private LAN bind is explicitly selected).
Start requires Docker Engine 28.0.0 or newer and Docker Compose v2.
Operator settings in config/spacecraft.json are seeded once and retained.
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
  PADO_CONFIG_GID="$(id -g)"
  [[ "$PADO_CONFIG_GID" =~ ^[0-9]+$ ]] || die 'Cannot determine the operator group ID.'
  export PADO_CONFIG_GID
  local command_name
  for command_name in docker iptables ip python3; do
    command -v "$command_name" >/dev/null || die "Missing required command: $command_name"
  done
  if (( EUID != 0 )); then
    command -v sudo >/dev/null || die 'sudo is required to manage DOCKER-USER.'
  fi
  docker info --format '{{.ServerVersion}}' >/dev/null || die 'Docker daemon is unavailable.'
  docker compose version >/dev/null || die 'Docker Compose v2 is required.'
  if [[ "$PADO_ENVIRONMENT" != legacy ]]; then
    prepare_profile
  fi
}

prepare_profile() {
  mkdir -p "$ROOT_DIR/out/deploy/$PADO_ENVIRONMENT"
  local profile_dir="$ROOT_DIR/out/deploy/$PADO_ENVIRONMENT"
  # A deployment nonce makes an earlier login's activation inapplicable after
  # restart. No Docker socket or container-management privilege enters the web.
  if [[ "${1:-${PADO_ACTION:-}}" == start || ! -f "$profile_dir/deployment-id" ]]; then
    python3 -c 'import secrets; print(secrets.token_hex(16))' > "$profile_dir/deployment-id"
  fi
  PADO_VALIDATION_DEPLOYMENT="$(cat "$profile_dir/deployment-id")"
  export PADO_VALIDATION_DEPLOYMENT
  docker compose -p pado-stage9-local -f "$BASE_COMPOSE_FILE" config --format json > "$profile_dir/base.json"
  PROFILE_ARGS=(--base "$profile_dir/base.json" --environment "$PADO_ENVIRONMENT"
    --deployment-id "$PADO_VALIDATION_DEPLOYMENT" --config-dir "$CONFIG_DIR"
    --bind-ip "$PADO_LOCAL_BIND_IP" --operator-cidr "$PADO_OPERATOR_CIDR")
  (cd "$ROOT_DIR" && python3 -m deploy.environment_profile render "${PROFILE_ARGS[@]}") > "$profile_dir/compose.json"
  COMPOSE_FILE="$profile_dir/compose.json"
}

prepare_spacecraft_config() {
  # Bind the directory so ROS atomic replacements remain visible to readers.
  # Touch only this dedicated directory and its runtime file, never the repo.
  python3 - "$CONFIG_DIR" "$PADO_CONFIG_GID" "$ROOT_DIR/config/spacecraft.example.json" <<'PY' || die 'Cannot prepare spacecraft.json; check its ownership, group and permissions.'
import os
from pathlib import Path
import stat
import sys

directory = Path(sys.argv[1])
gid = int(sys.argv[2])
actual = directory / "spacecraft.json"
template = Path(sys.argv[3])

def metadata(path, directory=False):
    info = path.lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid or stat.S_ISLNK(info.st_mode):
        raise ValueError(f"{path}: expected a non-symlink {'directory' if directory else 'regular file'}")
    if not directory and info.st_nlink != 1:
        raise ValueError(f"{path}: hard-linked settings files are not allowed")
    return info

def permissions(path, wanted, directory=False):
    info = metadata(path, directory)
    mode = stat.S_IMODE(info.st_mode)
    if info.st_gid == gid and mode == wanted:
        return
    # After an ROS update, UID 10001 owns the replacement. The operator can
    # still edit it through the shared group, but cannot chmod it; leave it.
    if os.geteuid() not in (0, info.st_uid):
        required = 0o2070 if directory else 0o060
        if info.st_gid == gid and mode & required == required and not mode & 0o002:
            return
        raise PermissionError(f"{path}: owner must set group {gid} and mode {wanted:04o}")
    if info.st_gid != gid:
        os.chown(path, -1, gid, follow_symlinks=False)
    os.chmod(path, wanted, follow_symlinks=False)

try:
    directory.mkdir(mode=0o775, exist_ok=True)
    metadata(directory, directory=True)
    metadata(template)
    permissions(directory, 0o2775, directory=True)
    try:
        metadata(actual)
    except FileNotFoundError:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(os.open(actual, flags, 0o664), "wb") as target:
            target.write(template.read_bytes())
            target.flush()
            os.fsync(target.fileno())
        print("pado-gs: Created config/spacecraft.json from the example settings.")
    permissions(actual, 0o664)
except (OSError, ValueError) as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(1)
PY
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
  python3 - "$PADO_LOCAL_BIND_IP" "$PADO_OPERATOR_CIDR" <<'PY' || die 'Invalid LAN/loopback bind address or operator CIDR.'
import ipaddress
import sys

try:
    bind = ipaddress.ip_address(sys.argv[1])
    operator = ipaddress.ip_network(sys.argv[2], strict=True)
    if bind.version != 4 or operator.version != 4:
        raise ValueError("IPv4 is required")
    lan_ranges = tuple(map(ipaddress.ip_network,
                           ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
    if str(bind) == "127.0.0.1":
        if str(operator) != "127.0.0.1/32":
            raise ValueError("loopback bind requires operator CIDR 127.0.0.1/32")
    elif not any(bind in lan for lan in lan_ranges) or not any(
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
' "$PADO_LOCAL_BIND_IP" || die "$PADO_LOCAL_BIND_IP is not assigned to this host. Select 127.0.0.1 or its current private IP."
}

firewall_available() {
  fw -S DOCKER-USER >/dev/null 2>&1 || die "Docker DOCKER-USER chain is unavailable; refusing to publish port $PORT without its access rule."
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
if os.environ.get('PADO_ENVIRONMENT') in ('validation','operations'):
    other_chain,other_mark,other_host,other_bridge = (
        ('PADO_GS_OPS','pado-gs-ops-http','pado-gs-ops-host','br-pado-ops') if chain=='PADO_GS_LOCAL'
        else ('PADO_GS_LOCAL','pado-gs-local-http','pado-gs-local-host','br-pado-local'))
    other_jump=['-A','DOCKER-USER','-m','comment','--comment',other_mark,'-j',other_chain]
    other_input=['-A','INPUT','-i',other_bridge,'-m','conntrack','!','--ctstate','ESTABLISHED,RELATED',
                 '-m','comment','--comment',other_host,'-j','DROP']
    docker=[r for r in docker if canonical(r)!=canonical(other_jump)]
    host=[r for r in host if canonical(r)!=canonical(other_input)]
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

runtime_services() {
  local services='dgs-ops-gateway portal ros-api mps-api ros-core-read mps-core-read sim-ground'
  [[ "$PADO_ENVIRONMENT" != operations ]] || services+=' fds-worker'
  [[ "$PADO_ENVIRONMENT" == operations ]] || services+=' fds-synthetic-demo sim-satellite'
  [[ "$PADO_ENVIRONMENT" != legacy ]] || services+=' fds-iss-display'
  printf '%s\n' "$services"
}

wait_for_services() {
  local attempt running service ready
  for attempt in {1..30}; do
    running="$(compose ps --status running --services)" || return 1
    running=$'\n'"$running"$'\n'
    ready=true
    local expected_services
    expected_services="$(runtime_services)"
    for service in $expected_services; do
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

startup_diagnostic_command() {
  local label="$1"
  shift
  note "$label"
  # Never expose full inspect/config/environment data. Each caller supplies a
  # fixed, read-only command, with bounded output and a short deadline.
  if ! python3 - "$@" <<'PY'
import subprocess
import sys

limit = 32768
try:
    result = subprocess.run(sys.argv[1:], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=8, check=False)
    output = result.stdout
    code = result.returncode
except subprocess.TimeoutExpired as error:
    output = error.stdout or b""
    code = 124
except OSError as error:
    output = str(error).encode("utf-8", errors="replace")
    code = 1
sys.stdout.write(output[:limit].decode("utf-8", errors="replace"))
if output and not output.endswith(b"\n"):
    sys.stdout.write("\n")
if len(output) > limit:
    print("[diagnostic output truncated]")
if code:
    print(f"[diagnostic command unavailable: exit {code}; cleanup continues]")
sys.exit(code != 0)
PY
  then
    note 'A diagnostic command failed; continuing startup rollback.'
  fi
  return 0
}

startup_diagnostics() {
  local stage="$1" container_id
  local -a container_ids=()
  note "Startup failed at stage: $stage. Capturing diagnostics before cleanup."
  startup_diagnostic_command 'Container status before cleanup:' \
    docker compose -p "$PROJECT" -f "$COMPOSE_FILE" ps --all
  startup_diagnostic_command 'Recent application/core logs (last 40 lines per service):' \
    docker compose -p "$PROJECT" -f "$COMPOSE_FILE" logs --no-color --tail 40 \
      $(runtime_services) sim-volume-init
  # Query only this Compose project's containers. No user-supplied ID or
  # unrestricted Docker inspect output is accepted here.
  while IFS= read -r container_id; do
    if [[ "$container_id" =~ ^[0-9a-f]{12,64}$ && ${#container_ids[@]} -lt 16 ]]; then
      container_ids+=("$container_id")
    fi
  done < <(python3 - "$PROJECT" "$COMPOSE_FILE" <<'PY'
import subprocess
import sys

try:
    result = subprocess.run(["docker", "compose", "-p", sys.argv[1], "-f", sys.argv[2],
                             "ps", "--all", "--quiet"], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=5, check=False)
    if result.returncode == 0:
        sys.stdout.write(result.stdout[:4096].decode("ascii", errors="ignore"))
except (OSError, subprocess.TimeoutExpired):
    pass
PY
  )
  if (( ${#container_ids[@]} )); then
    startup_diagnostic_command 'Container exit errors and healthcheck output:' \
      docker inspect --format \
      '{{.Name}} status={{.State.Status}} exit={{.State.ExitCode}} error={{json .State.Error}}{{if .State.Health}} health={{.State.Health.Status}}{{range .State.Health.Log}}{{printf "\n"}}health exit={{.ExitCode}} output={{printf "%.2000s" .Output}}{{end}}{{end}}' \
      "${container_ids[@]}"
  fi
  return 0
}

rollback_start() {
  local stage="${1:-unknown}"
  startup_diagnostics "$stage" || true
  if ! compose stop; then
    die 'Startup failed and containers could not be stopped. Firewall rule retained.'
  fi
  if ! compose down; then
    die 'Startup failed and containers or networks could not be removed. Firewall rule retained.'
  fi
  remove_firewall
  die "Startup failed at stage '$stage'; containers and networks removed, data volumes kept, firewall rule removed."
}

verify_runtime_networks() {
  local require_gateway="${1:-}"
  if [[ "$PADO_ENVIRONMENT" != legacy ]]; then
    docker network inspect "${PROJECT}_entry" "${PROJECT}_dgs_portal" "${PROJECT}_portal_ros" "${PROJECT}_portal_mps" "${PROJECT}_sim_tm" |
      (cd "$ROOT_DIR" && python3 -m deploy.environment_profile networks "${PROFILE_ARGS[@]}" ${require_gateway:+--require-gateway})
    return
  fi
  docker network inspect \
    "${PROJECT}_entry" "${PROJECT}_dgs_portal" \
    "${PROJECT}_portal_ros" "${PROJECT}_portal_mps" \
    "${PROJECT}_sim_tm" | \
    python3 "$ROOT_DIR/deploy/check_compose_policy.py" \
      --actual-networks --project "$PROJECT" --bridge "$ENTRY_BRIDGE" \
      --bind-ip "$PADO_LOCAL_BIND_IP" --port "$PORT" \
      ${require_gateway:+--require-gateway}
}

validate_compose_config() {
  if [[ "$PADO_ENVIRONMENT" != legacy ]]; then
    compose config --format json | (cd "$ROOT_DIR" && python3 -m deploy.environment_profile verify "${PROFILE_ARGS[@]}")
    return
  fi
  compose config --format json | python3 "$ROOT_DIR/deploy/check_compose_policy.py" \
    --bind-ip "$PADO_LOCAL_BIND_IP" --port "$PORT" --bridge "$ENTRY_BRIDGE" \
    --operator-cidr "$PADO_OPERATOR_CIDR" --project "$PROJECT"
}

start_stack() {
  require_tools
  require_patched_engine
  prepare_spacecraft_config
  # An earlier run may still have its port published even if Docker's firewall
  # was reset. Quiesce it before checking or changing access rules.
  compose stop
  # Recreate the bridge rather than trusting an older network with the same
  # Compose name and different gateway settings. Named data volumes remain.
  compose down --remove-orphans
  validate_lan
  firewall_available
  validate_compose_config || \
    die 'Compose topology violates the DGS-only external communication policy.'
  note 'Building local services...'
  if [[ "$PADO_ENVIRONMENT" == legacy ]]; then compose build
  elif [[ "$PADO_ENVIRONMENT" == validation ]]; then compose build portal sim-ground fds-synthetic-demo
  else compose build portal sim-ground fds-worker
  fi
  ensure_account
  install_firewall
  if ! compose create --no-build; then
    rollback_start compose-create
  fi
  if ! verify_runtime_networks; then
    rollback_start network-validation
  fi
  if ! compose up -d; then
    rollback_start compose-up
  fi
  if ! wait_for_services; then
    rollback_start services-running
  fi
  if ! wait_for_portal; then
    rollback_start portal-ready
  fi
  if ! firewall_rules_present; then
    rollback_start firewall-validation
  fi
  if ! verify_runtime_networks --require-gateway; then
    rollback_start gateway-network-validation
  fi
  if [[ "$PADO_LOCAL_BIND_IP" == 127.0.0.1 ]]; then
    note "Open http://127.0.0.1:$PORT/login on this Linux host, or use SSH local forwarding from the operator PC."
  else
    note "Open http://$PADO_LOCAL_BIND_IP:$PORT/login from the operator PC ($PADO_OPERATOR_CIDR)."
  fi
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

PADO_ACTION="${1:-}"
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
