#!/usr/bin/env bash
#
# launch.sh — cost-safe RunPod job wrapper for the primitive-pictures generation pipeline.
#
# Vendored (not depended on in place) from the runpod-jobs Claude Code skill's `scripts/rp`,
# per ADR-0001 (docs/adr/0001-runpod-batch-job-orchestration.md): this project must work for
# anyone who clones it, not only inside a Claude Code session with that skill installed. The
# mechanics are unchanged from the skill's script -- it was already fully project-agnostic --
# only this header and the project pointer below are new. If the skill's safety recipe changes,
# re-diff against ~/.claude/skills/runpod-jobs/scripts/rp and pull the fix in by hand.
#
# It rents a pod, runs a job on it under a POD-SIDE DEAD-MAN'S-SWITCH (so a disconnected laptop
# cannot leak a bill), and guarantees teardown. It shells out to `runpodctl` + the REST API and sets
# env vars; it hardcodes nothing project-specific. Configure entirely by environment (below).
#
# Cost safety is layered and does NOT depend on your machine staying connected:
#   * `up` bakes a max-lifetime ceiling + an idle watchdog into the pod's start command.
#   * `run` wraps your command in a trap that always terminates the pod.
#   * `down` TERMINATES (a stopped pod still bills) and is idempotent.
# Also set an account spend limit in the RunPod console — the coarse backstop.
#
# The RunPod CLI/REST surface MOVES. Verify shapes at run time (see the runpod-jobs skill refs).
# The API key is handled BY REFERENCE only — never echoed, logged, or placed in argv/a URL.
#
# pipeline/orchestrate.py drives this: `up`, `relay-get`, `down`. RP_JOB_CMD is built there by
# embedding pipeline/runpod/generate.py + config.py + the prompt payload directly into the
# command (never fetched over the network at boot -- see the skill's warning on this).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Config (env, with defaults). A gitignored ./.rp.env in the CWD is sourced if present. -----------
[ -f "${PWD}/.rp.env" ] && . "${PWD}/.rp.env"

POD_NAME="${RP_POD_NAME:-rp-job}"                 # fixed name tag -> `up` is idempotent, `down` targetable
POD_IMAGE="${RP_POD_IMAGE:-}"                     # REQUIRED: an image a pod can pull
JOB_CMD="${RP_JOB_CMD:-sleep infinity}"          # what the pod runs (default: idle keepalive to drive by hand)

COMPUTE_TYPE="${RP_COMPUTE_TYPE:-CPU}"            # CPU | GPU
CLOUD_TYPE="${RP_CLOUD_TYPE:-SECURE}"            # SECURE | COMMUNITY
INTERRUPTIBLE="${RP_INTERRUPTIBLE:-false}"       # RunPod discontinued spot pods (verified 2026-09-27 via
                                                  # a direct REST call: interruptible=true 500s with
                                                  # "Spot pods are no longer offered"). On-demand only for now.
CPU_FLAVORS="${RP_CPU_FLAVORS:-cpu5m,cpu3m}"      # tried in order (CPU)
VCPU_COUNT="${RP_VCPU_COUNT:-4}"                  # (CPU)
GPU_TYPES="${RP_GPU_TYPE:-}"                      # exact id(s), comma-sep (GPU). `runpodctl gpu list`'s
                                                   # gpuId values aren't all valid here -- MIG variants
                                                   # (e.g. "... MIG 1g.24gb") 400 on pod-create even though
                                                   # gpu list shows them. The pod-create endpoint's actual
                                                   # enum comes back in a 400's error body if you send an
                                                   # invalid one; that's the authoritative list.
GPU_COUNT="${RP_GPU_COUNT:-1}"                    # (GPU)
CONTAINER_DISK_GB="${RP_CONTAINER_DISK_GB:-20}"
VOLUME_GB="${RP_VOLUME_GB:-0}"                    # 0 = no persistent (billed) volume
POD_PORT="${RP_POD_PORT:-}"                       # optional inbound port, e.g. 8000; empty = none

MIN_RAM_GB="${RP_MIN_RAM_GB:-0}"                  # abort if the created pod has less (0 = skip check)
MAX_HOURLY_USD="${RP_MAX_HOURLY_USD:-1.00}"      # `up` refuses to launch/keep a pod above this
MAX_LIFETIME_SECS="${RP_MAX_LIFETIME_SECS:-3600}" # hard ceiling (raise well past a long job)
IDLE_TIMEOUT_SECS="${RP_IDLE_TIMEOUT_SECS:-900}"  # reap if the job log stops growing this long
IDLE_CHECK_SECS="${RP_IDLE_CHECK_SECS:-60}"

RUNPODCTL_VERSION="${RP_RUNPODCTL_VERSION:-v2.14.0}"   # pinned for the pod-side install (relay-list match)
API_BASE="${RP_API_BASE:-https://rest.runpod.io/v1}"
STATE_FILE="${RP_STATE_FILE:-${SCRIPT_DIR}/.rp-state}"

# Optional ssh for exec/push/pull (only usable if the pod gets a public IP). PUBLIC key is embedded;
# the private key never leaves this machine.
SSH_KEY="${RP_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_PUBKEY="${RP_SSH_PUBKEY:-${SSH_KEY}.pub}"
SSH_PUBKEY_CONTENT=""
[ -f "$SSH_PUBKEY" ] && SSH_PUBKEY_CONTENT="$(awk 'NF>=2{print $1" "$2; exit}' "$SSH_PUBKEY" 2>/dev/null || true)"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10)

log()  { printf 'rp: %s\n' "$*" >&2; }
die()  { printf 'rp: error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
require_key() { : "${RUNPOD_API_KEY:?set RUNPOD_API_KEY (a dedicated, limited-scope key)}"; }

# Call the REST API. The bearer token goes to curl via a config file on STDIN, never argv/a URL.
rp_api() {
  require_key
  local method="$1" path="$2" body="${3:-}" args=(--silent --show-error --fail-with-body -X "$1")
  [ -n "$body" ] && args+=(-H 'content-type: application/json' --data-binary "$body")
  printf 'header = "Authorization: Bearer %s"\n' "$RUNPOD_API_KEY" | curl "${args[@]}" -K - "${API_BASE}${path}"
}

json_string() { if have jq; then printf '%s' "$1" | jq -Rs .; else
  local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; printf '"%s"' "$s"; fi; }

find_pod_id() {
  local json; json="$(rp_api GET /pods || true)"; [ -n "$json" ] || return 0
  if have jq; then printf '%s' "$json" | jq -r --arg n "$POD_NAME" '.. | objects | select(.name==$n) | .id' 2>/dev/null | head -n1
  elif [ -f "$STATE_FILE" ]; then cat "$STATE_FILE"; fi
}
current_pod_id() { local id; id="$(find_pod_id || true)"; [ -z "$id" ] && [ -f "$STATE_FILE" ] && id="$(cat "$STATE_FILE")"
  [ -n "${id:-}" ] || die "no pod (run \`rp up\`)"; printf '%s' "$id"; }

# --- The pod-side dead-man's-switch + job, baked into dockerStartCmd (survives a laptop disconnect) ---
launch_command() {
  cat <<LAUNCH
set -eu
LOG=/tmp/rp-job.log ; : > "\$LOG"
if ! command -v runpodctl >/dev/null 2>&1; then
  ( wget -qO /usr/local/bin/runpodctl https://github.com/runpod/runpodctl/releases/download/${RUNPODCTL_VERSION}/runpodctl-linux-amd64 \
    || curl -fsSL -o /usr/local/bin/runpodctl https://github.com/runpod/runpodctl/releases/download/${RUNPODCTL_VERSION}/runpodctl-linux-amd64 ) \
    && chmod +x /usr/local/bin/runpodctl || echo "pod: no runpodctl (self-terminate falls back to the laptop trap + spend cap)" >&2
fi
terminate() {
  echo "pod: self-terminating (\$1)" >&2
  runpodctl pod delete "\$RUNPOD_POD_ID" >/dev/null 2>&1 || runpodctl remove pod "\$RUNPOD_POD_ID" >/dev/null 2>&1 || true
  kill -TERM -1 2>/dev/null || true
}
POD_SSH_PUBKEY='${SSH_PUBKEY_CONTENT}'
if [ -n "\$POD_SSH_PUBKEY" ]; then
  command -v sshd >/dev/null 2>&1 || (apt-get update && apt-get install -y --no-install-recommends openssh-server) >/dev/null 2>&1 || true
  mkdir -p /root/.ssh /run/sshd; printf '%s\n' "\$POD_SSH_PUBKEY" >> /root/.ssh/authorized_keys
  chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys
  sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config 2>/dev/null || true
  /usr/sbin/sshd 2>/dev/null || true
fi
# Guard 1: hard max-lifetime ceiling.
( sleep ${MAX_LIFETIME_SECS} ; terminate "max-lifetime ${MAX_LIFETIME_SECS}s" ) &
# The job, tee'd so the idle watchdog can watch its output grow.
( ${JOB_CMD} 2>&1 | tee "\$LOG" ) &
JOB=\$!
# Guard 2: idle watchdog on log growth (any output = alive).
(
  last=0 ; changed=\$(date +%s)
  while kill -0 "\$JOB" 2>/dev/null; do
    sleep ${IDLE_CHECK_SECS}
    size=\$(wc -c < "\$LOG" 2>/dev/null || echo 0) ; now=\$(date +%s)
    if [ "\$size" != "\$last" ]; then last=\$size ; changed=\$now
    elif [ \$(( now - changed )) -ge ${IDLE_TIMEOUT_SECS} ]; then terminate "idle ${IDLE_TIMEOUT_SECS}s" ; break ; fi
  done
) &
wait "\$JOB" ; terminate "job exited"
LAUNCH
}

cmd_up() {
  require_key
  [ -n "$POD_IMAGE" ] || die "set RP_POD_IMAGE"
  awk "BEGIN{exit !(${MAX_HOURLY_USD} <= 0)}" && die "set a positive RP_MAX_HOURLY_USD cap"
  local existing; existing="$(find_pod_id || true)"
  if [ -n "${existing:-}" ]; then log "reusing pod ${existing} (name=${POD_NAME})"; printf '%s' "$existing" >"$STATE_FILE"; printf '%s\n' "$existing"; return 0; fi

  local start_cmd ports_json compute_json
  start_cmd="$(launch_command)"
  ports_json=""; [ -n "$POD_PORT" ] && ports_json="\"${POD_PORT}/tcp\""
  [ -n "$SSH_PUBKEY_CONTENT" ] && ports_json="${ports_json:+${ports_json}, }\"22/tcp\""
  if [ "$COMPUTE_TYPE" = "GPU" ]; then
    [ -n "$GPU_TYPES" ] || die "set RP_GPU_TYPE (see: runpodctl gpu list)"
    local g; g="$(printf '%s' "$GPU_TYPES" | awk -F, '{for(i=1;i<=NF;i++) printf "%s\"%s\"",(i>1?",":""),$i}')"
    compute_json="\"computeType\":\"GPU\",\"gpuTypeIds\":[${g}],\"gpuCount\":${GPU_COUNT}"
  else
    local f; f="$(printf '%s' "$CPU_FLAVORS" | awk -F, '{for(i=1;i<=NF;i++) printf "%s\"%s\"",(i>1?",":""),$i}')"
    compute_json="\"computeType\":\"CPU\",\"cpuFlavorIds\":[${f}],\"vcpuCount\":${VCPU_COUNT}"
  fi
  log "creating ${CLOUD_TYPE} $([ "$INTERRUPTIBLE" = true ] && echo spot || echo on-demand) ${COMPUTE_TYPE} pod (image=${POD_IMAGE})"
  local body id resp
  body="{\"name\":\"${POD_NAME}\",\"imageName\":\"${POD_IMAGE}\",${compute_json},\"cloudType\":\"${CLOUD_TYPE}\",\"interruptible\":${INTERRUPTIBLE},\"containerDiskInGb\":${CONTAINER_DISK_GB},\"volumeInGb\":${VOLUME_GB},\"ports\":[${ports_json}],\"dockerStartCmd\":[\"bash\",\"-lc\",$(json_string "$start_cmd")]}"
  resp="$(rp_api POST /pods "$body")"
  id="$(printf '%s' "$resp" | jq -r '.id // .pod.id // empty' 2>/dev/null || true)"
  [ -n "${id:-}" ] || die "create returned no id (verify the REST shape; response withheld to avoid leaking anything)"
  printf '%s' "$id" >"$STATE_FILE"; log "created pod ${id}"

  # Verify what we actually got: enough RAM, within the cap. A wrong flavor is caught here for cents.
  if have jq; then
    local info ram cost; info="$(rp_api GET "/pods/${id}" || true)"
    ram="$(printf '%s' "$info"  | jq -r '.. | (.memoryInGb? // .memoryGb? // empty)' 2>/dev/null | head -1)"
    cost="$(printf '%s' "$info" | jq -r '.. | (.costPerHr? // empty)' 2>/dev/null | head -1)"
    if [ -n "$ram" ] && awk "BEGIN{exit !(${ram} < ${MIN_RAM_GB})}"; then
      rp_api DELETE "/pods/${id}" >/dev/null 2>&1 || true; rm -f "$STATE_FILE"; die "pod had ${ram} GB RAM < ${MIN_RAM_GB}; terminated"; fi
    if [ -n "$cost" ] && awk "BEGIN{exit !(${cost} > ${MAX_HOURLY_USD})}"; then
      rp_api DELETE "/pods/${id}" >/dev/null 2>&1 || true; rm -f "$STATE_FILE"; die "pod cost \$${cost}/hr > cap \$${MAX_HOURLY_USD}; terminated"; fi
    [ -n "$ram" ] && log "pod ${id}: ${ram} GB RAM"; [ -n "$cost" ] && log "pod ${id}: \$${cost}/hr"
  fi
  printf '%s\n' "$id"
}

cmd_down() {
  trap - EXIT INT TERM
  local id=""; [ -n "${RUNPOD_API_KEY:-}" ] && id="$(find_pod_id || true)"
  [ -z "$id" ] && [ -f "$STATE_FILE" ] && id="$(cat "$STATE_FILE")"
  [ -z "$id" ] && { log "no pod recorded; nothing to terminate"; return 0; }
  require_key; log "terminating pod ${id}"
  rp_api DELETE "/pods/${id}" >/dev/null 2>&1 || (have runpodctl && runpodctl pod delete "$id" >/dev/null 2>&1) || log "pod ${id} already gone (ok)"
  rm -f "$STATE_FILE"
  # Verify the account is actually clear (layer 4): the whole failure mode is a pod left running.
  local still; still="$(find_pod_id || true)"
  if [ -n "$still" ]; then log "WARNING: pod ${still} still present after delete — retry \`rp down\` or check the console"; else log "verified: no '${POD_NAME}' pod remains"; fi
}

cmd_logs() {
  require_key; local id; id="$(current_pod_id)"
  RUNPOD_API_KEY="${RUNPOD_API_KEY}" runpodctl pod logs "$id" "$@"
}

cmd_status() { require_key; local id; id="$(current_pod_id)"
  if have jq; then rp_api GET "/pods/${id}" | jq -r '"pod \(.id): \(.desiredStatus // .status // "?") \(.costPerHr // "")"' 2>/dev/null || rp_api GET "/pods/${id}"
  else rp_api GET "/pods/${id}"; fi; }

cmd_run() { [ "$#" -ge 1 ] || die "usage: rp run <cmd...>"
  local id; id="$(current_pod_id)"
  trap 'cmd_down' EXIT INT TERM     # always terminate, whatever happens to <cmd>
  RP_POD_ID="$id" "$@"; }

# ssh transport (only works if the pod got a public IP — spot pods often don't; see data-transport.md).
ssh_host_port() { local info ip port; info="$(rp_api GET "/pods/$1" || true)"
  ip="$(printf '%s' "$info" | jq -r '.publicIp // empty' 2>/dev/null | head -1)"
  port="$(printf '%s' "$info" | jq -r '(.portMappings["22"] // empty)|tostring' 2>/dev/null | head -1)"
  { [ -n "$ip" ] && [ -n "$port" ] && [ "$port" != null ]; } || return 0; printf '%s %s' "$ip" "$port"; }
resolve_ssh() { local hp; hp="$(ssh_host_port "$(current_pod_id)")"
  [ -n "$hp" ] || die "no ssh: the pod has no public IP (spot pods often don't). Use relay/pod-logs (data-transport.md)"; printf '%s' "$hp"; }
cmd_exec() { [ -f "$SSH_KEY" ] || die "no ssh key at $SSH_KEY"; local hp; hp="$(resolve_ssh)"; ssh "${SSH_OPTS[@]}" -i "$SSH_KEY" -p "${hp#* }" "root@${hp% *}" "$@"; }
cmd_push() { [ "$#" -ge 2 ] || die "usage: rp push <local> <remote>"; local hp; hp="$(resolve_ssh)"; scp "${SSH_OPTS[@]}" -i "$SSH_KEY" -P "${hp#* }" -r "$1" "root@${hp% *}:$2"; }
cmd_pull() { [ "$#" -ge 2 ] || die "usage: rp pull <remote> <local>"; local hp; hp="$(resolve_ssh)"; scp "${SSH_OPTS[@]}" -i "$SSH_KEY" -P "${hp#* }" -r "root@${hp% *}:$1" "$2"; }

# Pull an artifact OFF a pod over the runpodctl relay — needs NO public IP (the transport that works
# when ssh can't reach the pod). Convention: the JOB, when done, does
#   runpodctl send --code <base> out.tar > /tmp/s.log 2>&1 &
#   echo "RP_ARTIFACT_CODE=$(grep -oE '<base>[-0-9]+' /tmp/s.log | head -1)"   # then `wait`
# so the sender's random final code reaches us via `pod logs`. See references/data-transport.md.
cmd_relay_get() {
  [ "$#" -ge 1 ] || die "usage: rp relay-get <local-dest-dir>"
  require_key; have runpodctl || die "runpodctl required for relay-get"
  local id dest podlog code deadline; id="$(current_pod_id)"; dest="$1"; mkdir -p "$dest"
  podlog="$(mktemp)"; deadline=$(( $(date +%s) + MAX_LIFETIME_SECS )); code=""
  log "waiting for the job to publish RP_ARTIFACT_CODE=… in the pod logs"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    RUNPOD_API_KEY="${RUNPOD_API_KEY}" runpodctl pod logs "$id" --source container --tail 5000 --max-wait 5s > "$podlog" 2>&1 || true
    code="$(grep -oE 'RP_ARTIFACT_CODE=[A-Za-z0-9-]+' "$podlog" | head -1 | cut -d= -f2 || true)"
    [ -n "$code" ] && break
    sleep 10
  done
  rm -f "$podlog"
  [ -n "$code" ] || die "no RP_ARTIFACT_CODE in the pod logs within ${MAX_LIFETIME_SECS}s (did the job send + echo it? see data-transport.md)"
  log "artifact code seen; receiving over the relay -> ${dest}"
  ( cd "$dest" && runpodctl receive "$code" )
}

usage() { cat >&2 <<'U'
rp — cost-safe RunPod job wrapper. Configure by env (RP_POD_IMAGE, RP_JOB_CMD, RP_MAX_HOURLY_USD,
RP_MAX_LIFETIME_SECS, RP_COMPUTE_TYPE=CPU|GPU, RP_GPU_TYPE/…). A ./.rp.env in the CWD is sourced.

  rp up                 create-or-reuse a spot pod (dead-man's-switch armed), print id
  rp logs [flags]       pod logs (add --follow, --tail N; works with no public IP)
  rp run <cmd...>       run <cmd> locally inside a trap that always terminates the pod
  rp status             pod status
  rp exec|push|pull …   ssh transport (only if the pod got a public IP)
  rp relay-get <dir>    pull an artifact off the pod over the relay (NO public IP needed; the job must
                        `runpodctl send` it and echo RP_ARTIFACT_CODE=… — see data-transport.md)
  rp down               TERMINATE the pod (idempotent)

ALWAYS also set an account spend limit in the RunPod console. RunPod's surface moves — verify verbs.
U
}

main() { local sub="${1:-}"; [ "$#" -gt 0 ] && shift || true
  case "$sub" in
    up) cmd_up "$@";; down) cmd_down "$@";; logs) cmd_logs "$@";; run) cmd_run "$@";;
    status) cmd_status "$@";; exec) cmd_exec "$@";; push) cmd_push "$@";; pull) cmd_pull "$@";;
    relay-get) cmd_relay_get "$@";;
    ''|-h|--help|help) usage;; *) usage; die "unknown subcommand: ${sub}";;
  esac; }
main "$@"
