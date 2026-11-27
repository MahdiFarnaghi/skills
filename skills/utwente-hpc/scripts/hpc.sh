#!/usr/bin/env bash
# hpc.sh — thin SSH + Slurm wrapper for the University of Twente EEMCS-HPC cluster.
#
# Every remote command runs on a HEAD NODE inside a login shell (bash -ls) and is
# fed over stdin. That way `module load`, the UT proxy variables and the full PATH
# are available, and quoting can never break. Nothing here logs into compute nodes.
#
# Usage:  hpc.sh [--host ALIAS|auto] [--dry-run] <command> [args...]
#
# Choosing the machine
#   --host hpc1|hpc2|auto       head node to log in to (default: $HPC_HOST or hpc1;
#                               `auto` picks the first reachable of $HPC_HOSTS, default "hpc1 hpc2")
#   machines                    node classes you may use: GPUs (model, VRAM), CPUs, RAM, idle counts
#   pick [requirements]         recommend partition + #SBATCH header for a job (see `pick --help`)
#   busyness [--days N] [--json]
#                                occupancy per node class by weekday/4h-window, queue wait per partition,
#                                pending demand — from a week of accounting (all users); ~3 s on the head node
#
# Commands
#   run '<commands>'             Run shell commands on the head node (login shell).
#   run - [args...] < script     Same, reading the script from stdin ($1 $2 ... = args).
#   up <local> <remote>          rsync local -> cluster. Trailing slash on a dir copies
#                                 its contents (plain rsync semantics). RSYNC_OPTS = extras.
#   down <remote> <local>        rsync cluster -> local.
#   test <file.sbatch> [-- sbatch args]
#                                 Validate a LOCAL script: refuses unfilled {{PLACEHOLDERS}}, then
#                                 `sbatch --test-only` via stdin (nothing copied, no job created).
#   submit <remote dir> <script> [--test-only] [-- sbatch args]
#                                 sbatch an already-synced script from inside <remote dir> (its
#                                 submit dir); creates logs/ there; prints "JOBID=<n>".
#   status [jobid ...]           Your queue; with job ids also sacct details.
#   logs <jobid> [--tail N]      Show a job's stdout/stderr file(s) (default tail 80).
#   wait <jobid> [--timeout S] [--poll S]
#                                 Block (bounded, default 300s) until the job finishes.
#   cancel <jobid ...>           scancel.
#   srun [srun opts] -- <cmd>    Run one command on a compute node now (blocks; may queue).
#   info                         One-screen overview: identity, QOS, partitions you
#                                may use, node classes, your queue.
#   modules [group]              List module groups or versions (never `module avail`).
#   tty '<cmd>'                  Allocate a real TTY (sinteractive, top-node, ...).
#
# Environment
#   HPC_HOST        SSH config alias of a head node, or `auto` (default: hpc1). --host overrides.
#   HPC_HOSTS       Space-separated aliases for `auto` (default: "hpc1 hpc2").
#   HPC_REMOTE_DIR  (unused by default; set your project dir for convenience).
#   RSYNC_OPTS      Extra rsync flags, e.g. "--exclude=.git --exclude=data/".
#   HPC_PICK_DEFAULTS  Extra default flags for `pick` (e.g. "--avoid itc-gpu"), applied BEFORE the
#                 command line so explicit flags win. Same for lines of ~/.config/hpc/pick.conf
#                 (one flag per line or per line; "#" starts a comment). HPC_PICK_CONF overrides the path.
set -euo pipefail

HPC_HOST="${HPC_HOST:-hpc1}"
HPC_HOSTS="${HPC_HOSTS:-hpc1 hpc2}"
DRY_RUN=0
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=4)

usage() { awk 'NR > 1 && !/^#/ { exit } NR > 1' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
die()   { printf 'hpc: %s\n' "$*" >&2; exit 1; }

# --- option parsing ------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)        [[ $# -ge 2 ]] || die "--host needs a value"; HPC_HOST="$2"; shift 2 ;;
    --dry-run|-n)  DRY_RUN=1; shift ;;
    -h|--help)     usage 0 ;;
    --)            shift; break ;;
    -*)            die "unknown option: $1 (see --help)" ;;
    *)             break ;;
  esac
done
[[ $# -ge 1 ]] || usage 1
CMD="$1"; shift

# --- head-node selection --------------------------------------------------------
# Both head nodes are identical; `auto` picks the first one that accepts a connection.
if [[ $HPC_HOST == auto ]]; then
  HPC_HOST=""
  for h in $HPC_HOSTS; do
    if ssh -o BatchMode=yes -o ConnectTimeout=8 "$h" true </dev/null >/dev/null 2>&1; then HPC_HOST=$h; break; fi
  done
  [[ -n "$HPC_HOST" ]] || die "no reachable head node among: $HPC_HOSTS"
fi

# --- helpers -------------------------------------------------------------------
# Run a script body on the head node inside a login shell; the body travels via
# stdin. Any extra arguments become $1 $2 ... of the remote script.
remote_bash() {
  local body="$1" set_args="" a rc
  shift
  for a in "$@"; do set_args+=" $(printf '%q' "$a")"; done
  if [[ -n "$set_args" ]]; then
    # %q-escaped args arrive verbatim; re-expand a leading ~/ against the REMOTE $HOME.
    local fix='__h=("$@"); for __i in "${!__h[@]}"; do case ${__h[$__i]} in "~"/*) __h[$__i]="$HOME/${__h[$__i]#\~/}";; "~") __h[$__i]="$HOME";; esac; done; set -- "${__h[@]}"'
    body="set --$set_args
$fix
$body"
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '[dry-run] ssh %s bash -ls <<'"'"'SCRIPT'"'"'\n%s\nSCRIPT\n' "$HPC_HOST" "$body"
    return 0
  fi
  printf '%s\n' "$body" | ssh "${SSH_OPTS[@]}" "$HPC_HOST" 'bash -ls' && return
  rc=$?
  if [[ $rc -eq 255 ]]; then
    printf 'hpc: ssh to %s failed. Check that the host is up and your key is loaded in the agent\n  (try: ssh-add --apple-use-keychain ~/.ssh/id_hpc  |  or: --host %s  |  or: --host auto)\n' \
      "$HPC_HOST" "$(tr ' ' '\n' <<<"$HPC_HOSTS" | grep -vx "$HPC_HOST" | head -1)" >&2
  fi
  exit "$rc"
}

# rsync between local and cluster, reusing the ssh options.
rsync_to() { # up|down  src  dst   (dst is always a directory and is created if missing)
  local dir="$1" src="$2" dst="$3" args=(-az --human-readable)
  [[ $DRY_RUN -eq 1 ]] && args+=(--dry-run)
  # shellcheck disable=SC2206  # RSYNC_OPTS is intentionally word-split
  [[ -n "${RSYNC_OPTS:-}" ]] && args+=(${RSYNC_OPTS})
  if [[ $dir == up ]]; then
    [[ -e "$src" ]] || die "no such local path: $src"
    [[ $DRY_RUN -eq 1 ]] || remote_bash 'mkdir -p "$1"' "${dst%/}"
    rsync -e "ssh ${SSH_OPTS[*]}" "${args[@]}" "$src" "$HPC_HOST:${dst%/}/"
  else
    [[ $DRY_RUN -eq 1 ]] || mkdir -p "$dst"
    rsync -e "ssh ${SSH_OPTS[*]}" "${args[@]}" "$HPC_HOST:$src" "$dst/"
  fi
}

job_ids=()
collect_ids() {
  local a
  for a in "$@"; do
    [[ "$a" =~ ^[0-9]+(_[0-9]+)?$ ]] || die "not a job id: $a"
    job_ids+=("$a")
  done
}

# Refuse to touch the cluster with a template whose {{PLACEHOLDERS}} are unfilled.
check_placeholders() { # $1 = local file
  local hits
  hits=$(grep -nE '\{\{[A-Z_]+\}\}' "$1" || true)
  if [[ -n "$hits" ]]; then
    printf 'hpc: unfilled placeholders in %s (fill them, or delete the lines):\n%s\n' "$1" "$hits" >&2
    exit 1
  fi
}

# Remote snippet (bash 5 on the cluster): the partitions this account may use, and the
# node classes in them with per-class node counts by state. Sets: allowed[], P, and the
# associative arrays CNT IDLE BUSY OTHER PARTS keyed by "<cpus>|<memMB>|<gres>|<features>".
NODE_CLASSES='
accts=$(sacctmgr -nP show assoc user="$USER" format=account </dev/null 2>/dev/null | sort -u | paste -sd, -)
allowed=()
for p in $(sinfo -h -o %P </dev/null 2>/dev/null | tr -d "*" | sort -u); do
  aa=$(scontrol show partition "$p" </dev/null 2>/dev/null | grep -oE "AllowAccounts=[^ ]+" | cut -d= -f2-)
  ok=0; [[ "$aa" == ALL ]] && ok=1
  IFS="," read -ra list <<< "$aa"
  for a in "${list[@]}"; do [[ ",$accts," == *",$a,"* ]] && ok=1; done
  [[ $ok -eq 1 ]] && allowed+=("$p")
done
P=$(IFS=,; echo "${allowed[*]}")
declare -A CNT IDLE BUSY OTHER PARTS NP SEEN=()
if [[ ${#allowed[@]} -gt 0 ]]; then
  # sinfo -N prints one row per node AND partition: accumulate partitions, count each node once.
  while IFS="|" read -r n parts; do parts=${parts//\*/}; NP[$n]="${NP[$n]:-}$parts,"; done < <(sinfo -h -p "$P" -N -o "%N|%P" </dev/null 2>/dev/null)
  while IFS="|" read -r n c m g f t; do
    [[ -n "${NP[$n]:-}" ]] || continue
    [[ -z "${SEEN[$n]:-}" ]] || continue; SEEN[$n]=1
    key="$c|$m|$g|$f"
    CNT[$key]=$(( ${CNT[$key]:-0} + 1 ))
    case $t in idle) IDLE[$key]=$(( ${IDLE[$key]:-0} + 1 ));; allocated|mixed) BUSY[$key]=$(( ${BUSY[$key]:-0} + 1 ));; *) OTHER[$key]=$(( ${OTHER[$key]:-0} + 1 ));; esac
    PARTS[$key]="${PARTS[$key]:-}${NP[$n]},"
  done < <(sinfo -h -N -o "%N|%c|%m|%G|%f|%T" </dev/null 2>/dev/null)
fi
# VRAM per GPU model (GB) and the Slurm family it belongs to.
declare -A VRAM FAMILY
for kv in l40:48 l40s:48 a40:48 a100:40 h200nvl:141 rtx6000pro:96 rtx-6000:24 rtx-2080ti:8 t4:16; do
  VRAM[${kv%%:*}]=${kv##*:}
done
FAMILY[l40]=lovelace; FAMILY[l40s]=lovelace; FAMILY[a40]=ampere; FAMILY[a100]=ampere
FAMILY[h200nvl]=hopper; FAMILY[rtx6000pro]=blackwell; FAMILY[rtx-6000]=turing; FAMILY[rtx-2080ti]=turing; FAMILY[t4]=turing
parse_gres() { # $1 = gres string -> sets GF (family or -), GN (gpus per node or 0)
  GF=- GN=0
  [[ "$1" == gpu:* ]] || return
  rest=${1#gpu:}; GF=${rest%%:*}; rest=${rest#*:}; GN=${rest%%(*}; GN=${GN%%,*}
  [[ "$GN" =~ ^[0-9]+$ ]] || GN=0
}
model_of() { # $1 = features -> prints the GPU model tag or -
  local m; for m in "${!VRAM[@]}"; do [[ ",$1," == *",$m,"* ]] && { echo "$m"; return; }; done; echo -
}
'

# --- commands ------------------------------------------------------------------
case "$CMD" in
  run)
    if [[ $# -eq 0 || "$1" == "-" ]]; then
      [[ -t 0 ]] && die "run: give a command string, or 'run -' with a script on stdin"
      [[ $# -ge 1 ]] && shift
      remote_bash "$(cat)" "$@"
    else
      remote_bash "$1"
    fi ;;

  up)   [[ $# -eq 2 ]] || die "up <local> <remote dir>";  rsync_to up   "$1" "$2" ;;
  down) [[ $# -eq 2 ]] || die "down <remote> <local dir>"; rsync_to down "$1" "$2" ;;

  test)
    file="" sbatch_args=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --)   shift; sbatch_args=("$@"); break ;;
        -*)   die "unknown test option: $1" ;;
        *)    file="$1"; shift ;;
      esac
    done
    [[ -n "$file" ]] || die "test <file.sbatch> [-- sbatch args]"
    [[ -f "$file" ]] || die "no such file: $file"
    check_placeholders "$file"
    if [[ $DRY_RUN -eq 1 ]]; then
      printf '[dry-run] ssh %s bash -lc "cd && sbatch --test-only %s" < %s\n' "$HPC_HOST" "${sbatch_args[*]:-}" "$file"; exit 0
    fi
    # The script itself is sbatch's stdin, so the remote command is not stdin-fed here.
    ssh "${SSH_OPTS[@]}" "$HPC_HOST" "cd && sbatch --test-only ${sbatch_args[*]:-}" < "$file" 2>&1 ;;

  submit)
    remote_dir="" script="" test_only="" sbatch_args=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --in)        remote_dir="$2"; shift 2 ;;          # backwards-compatible alias for the positional dir
        --test-only) test_only="--test-only"; shift ;;
        --)          shift; sbatch_args=("$@"); break ;;
        -*)          die "unknown submit option: $1" ;;
        *)           if [[ -z "$remote_dir" ]]; then remote_dir="$1"; else script="$1"; fi; shift ;;
      esac
    done
    [[ -n "$remote_dir" && -n "$script" ]] || die "submit <remote dir> <script> [--test-only] [-- sbatch args]   (sync the script first with: hpc up)"
    remote_bash 'dir=$1 f=$2 to=$3; shift 3
cd "$dir" || exit 1
[[ -f "$f" ]] || { echo "no such script on the cluster: $dir/$f (sync it first: hpc up <local> $dir)"; exit 1; }
mkdir -p logs
printf "sbatch %s%s %s (from %s on %s)\n" "$to" "${*:+ $*}" "$f" "$dir" "$(hostname)"
out=$(sbatch $to "$@" "$f" </dev/null 2>&1) || { echo "$out"; exit 1; }
echo "$out"
[[ "$out" =~ Submitted\ batch\ job\ ([0-9]+) ]] && echo "JOBID=${BASH_REMATCH[1]}"
[[ "$out" =~ Job\ ([0-9]+)\ to\ start ]] && echo "ESTIMATE_JOBID=${BASH_REMATCH[1]}"' \
      "$remote_dir" "$script" "$test_only" ${sbatch_args[@]+"${sbatch_args[@]}"} ;;

  status)
    collect_ids "$@"
    remote_bash 'echo "-- queue for $USER@$(hostname)  ($(date +%F\ %T))"
squeue -u "$USER" -o "%.10i %.12P %.36j %.3t %.11M %.11l %.5D %.26R %.19S" </dev/null
if [[ $# -gt 0 ]]; then
  echo "-- accounting"
  sacct -j "$(IFS=,; echo "$*")" -X -o JobID%12,JobName%28,Partition%10,State%24,Elapsed%11,ExitCode,MaxRSS%12,NodeList%16 </dev/null
fi' ${job_ids[@]+"${job_ids[@]}"} ;;

  logs)
    [[ $# -ge 1 ]] || die "logs <jobid> [--tail N]"
    id="" tail_n=80
    while [[ $# -gt 0 ]]; do case "$1" in --tail) tail_n="$2"; shift 2;; *) id="$1"; shift;; esac; done
    collect_ids "$id"
    remote_bash 'id=$1 tail_n=$2
wd="" out=""
if live=$(scontrol show job "$id" 2>/dev/null); then
  out=$(grep -oE "StdOut=[^ ]+" <<<"$live" | cut -d= -f2-)
  wd=$(grep -oE "WorkDir=[^ ]+" <<<"$live" | cut -d= -f2-)
fi
[[ -z "$wd" ]] && wd=$(sacct -nP -j "$id" -X -o WorkDir%400 </dev/null 2>/dev/null | head -1)
[[ -z "$wd" ]] && wd=.
sacct -j "$id" -X -o JobID%12,JobName%28,State%24,Elapsed%11,ExitCode </dev/null 2>/dev/null
echo "-- work dir: $wd"
files=()
if [[ -n "$out" && -f "$out" ]]; then files=("$out"); else
  while IFS= read -r f; do files+=("$f"); done < <(find "$wd" -maxdepth 3 -type f \( -name "*${id}*.out" -o -name "*${id}*.err" -o -name "*${id}*.log" \) 2>/dev/null | sort | head -20)
fi
if [[ ${#files[@]} -eq 0 ]]; then
  echo "-- no output file found yet (still pending? or --output uses a pattern this script does not know)"; exit 3
fi
for f in "${files[@]}"; do
  echo "===== $f ($(wc -l < "$f" | tr -d " ") lines; showing last $tail_n) ====="
  tail -n "$tail_n" "$f"
done' "${job_ids[0]}" "$tail_n" ;;

  wait)
    [[ $# -ge 1 ]] || die "wait <jobid> [--timeout S] [--poll S]"
    id="" timeout_s=300 poll_s=10
    while [[ $# -gt 0 ]]; do case "$1" in --timeout) timeout_s="$2"; shift 2;; --poll) poll_s="$2"; shift 2;; *) id="$1"; shift;; esac; done
    collect_ids "$id"
    remote_bash 'id=$1 timeout_s=$2 poll_s=$3
start=$(date +%s)
while squeue -h -j "$id" </dev/null 2>/dev/null | grep -q .; do
  if (( $(date +%s) - start >= timeout_s )); then
    echo "still in queue after ${timeout_s}s:"; squeue -j "$id" -o "%.10i %.3t %.11M %.30R" </dev/null; exit 2
  fi
  sleep "$poll_s"
done
st=""   # accounting lags a few seconds behind the queue
for i in 1 2 3 4 5 6; do
  st=$(sacct -nP -j "$id" -X -o State </dev/null 2>/dev/null | head -1)
  [[ -n "$st" && "$st" =~ (COMPLETED|FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY|NODE_FAIL|PREEMPTED|BOOT_FAIL|DEADLINE) ]] && break
  if [[ -z "$st" && $i -ge 2 ]]; then echo "job $id is neither in the queue nor in accounting (wrong id?)"; exit 1; fi
  sleep 5
done
sacct -j "$id" -X -o JobID%12,JobName%28,State%24,Elapsed%11,ExitCode,MaxRSS%12,NodeList%16 </dev/null
[[ "$st" == COMPLETED* ]]' "${job_ids[0]}" "$timeout_s" "$poll_s" ;;

  cancel)
    [[ $# -ge 1 ]] || die "cancel <jobid ...>"
    collect_ids "$@"
    remote_bash 'scancel "$@" </dev/null && echo "cancelled: $*"' ${job_ids[@]+"${job_ids[@]}"} ;;

  srun)
    [[ $# -ge 2 ]] || die "srun [srun opts] -- <cmd>"
    opts=() cmd=""
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == "--" ]]; then shift; cmd="$*"; break; fi
      opts+=("$1"); shift
    done
    [[ -n "$cmd" ]] || die "srun: put -- before the command"
    remote_bash 'args=("$@"); cmd=${args[$(( ${#args[@]} - 1 ))]}; unset "args[$(( ${#args[@]} - 1 ))]"
echo "srun ${args[*]} bash -c $cmd   (waiting for allocation; Ctrl-C aborts)"
srun "${args[@]}" bash -c "$cmd" </dev/null' ${opts[@]+"${opts[@]}"} "$cmd" ;;

  info)
    remote_bash "$NODE_CLASSES"'
echo "== $(hostname)  user=$USER  $(sinfo -V 2>/dev/null)  $(date +%F\ %T)"
echo "-- accounts / QOS"
sacctmgr -nP show assoc user="$USER" format=account,partition,qos,defaultqos </dev/null 2>/dev/null | awk -F"|" "{printf \"   account=%-12s partition=%-10s qos=%-22s default=%s\n\",\$1,(\$2?\$2:\"-\"),\$3,\$4}"
for q in $(sacctmgr -nP show assoc user="$USER" format=qos </dev/null 2>/dev/null | tr ",|" "\\n\\n" | sort -u); do
  sacctmgr -nP show qos name="$q" format=name,maxwall,maxtrespu,maxjobspu,maxsubmitpu </dev/null 2>/dev/null | awk -F"|" "{printf \"   qos %-16s maxwall=%-10s maxtres/pu=%-12s maxjobs/pu=%-4s maxsubmit/pu=%s\n\",\$1,(\$2?\$2:\"-\"),(\$3?\$3:\"-\"),(\$4?\$4:\"-\"),(\$5?\$5:\"-\")}"
done
echo "-- partitions you may use (your accounts: $accts): ${allowed[*]:-NONE}"
echo "-- nodes allocated/idle/other/total per partition"
sinfo -s -h -p "$P" </dev/null 2>/dev/null | awk "{printf \"   %-12s %s\n\",\$1,\$4}"
echo "-- node classes you may use  (idle/busy/other of total nodes)"
printf "   %-14s %-5s %-9s %-6s %-9s %-7s %-8s %s\n" "GPU model" "VRAM" "GPU/node" "fam." "threads" "RAM GB" "idle" "partitions"
for key in "${!CNT[@]}"; do
  IFS="|" read -r c m g f <<<"$key"; parse_gres "$g"; model=$(model_of "$f")
  vram=${VRAM[$model]:-}
  parts=$(tr "," "\n" <<<"${PARTS[$key]}" | grep -v "^$" | sort -u | paste -sd, -)
  printf "   %-14s %-5s %-9s %-6s %-9s %-7s %-8s %s\n" "$([[ $model == - ]] && echo "-" || echo "$model")" "${vram:--}" "$GN" "$([[ $GF == - ]] && echo "-" || echo "$GF")" "$c" "$(( m / 1024 ))" "${IDLE[$key]:-0}/${BUSY[$key]:-0}/${OTHER[$key]:-0} of ${CNT[$key]}" "$parts"
done | sort -k2,2nr -k6,6n
echo "-- your queue ($(squeue -h -u "$USER" </dev/null 2>/dev/null | wc -l | tr -d " ") jobs)"
squeue -u "$USER" -o "   %.10i %.12P %.36j %.3t %.11M %.11l %.26R" </dev/null 2>/dev/null
' ;;

  machines)
    remote_bash "$NODE_CLASSES"'
printf "   %-14s %-5s %-9s %-6s %-9s %-8s %-14s %s\n" "GPU model" "VRAM" "GPU/node" "fam." "threads" "RAM GB" "idle/busy/oth." "partitions (submit to)"
for key in "${!CNT[@]}"; do
  IFS="|" read -r c m g f <<<"$key"; parse_gres "$g"; model=$(model_of "$f")
  parts=$(tr "," "\n" <<<"${PARTS[$key]}" | grep -v "^$" | sort -u)
  best=$(grep -vx main <<<"$parts" | head -1); [[ -n "$best" ]] || best=main
  printf "   %-14s %-5s %-9s %-6s %-9s %-8s %-14s %s\n" "$([[ $model == - ]] && echo "(none)" || echo "$model")" "${VRAM[$model]:--}" "$GN" "$([[ $GF == - ]] && echo "-" || echo "$GF")" "$c" "$(( m / 1024 ))" "${IDLE[$key]:-0}/${BUSY[$key]:-0}/${OTHER[$key]:-0} of ${CNT[$key]}" "$(sort -u <<<"$best
$parts" | paste -sd, -)"
done | sort -k3,3nr -k6,6n
echo "legend: nodes are exclusive per job; VRAM is per GPU card; idle = free right now (queues move; sbatch --test-only gives a start estimate)."
echo "pick one: hpc pick --vram <GB> --gpus <N> [--gpu-model l40s] --cpus <N> --mem <GB>   |   hpc pick --cpu-only --cpus <N> --mem <GB>"
' ;;

  pick)
    gpus="" vram="" model="" family="" cpus="1" mem="1" cpu_only=0 parts="" limit=5 avoid="" prefer="" busy=1 bdays=7
    # Personal defaults (low precedence): pick.conf lines, then HPC_PICK_DEFAULTS, then the real arguments.
    defaults=() dsrc=""
    conf="${HPC_PICK_CONF:-$HOME/.config/hpc/pick.conf}"
    if [[ -f "$conf" ]]; then
      while IFS= read -r line; do line="${line%%#*}"; [[ -n "${line//[[:space:]]/}" ]] && defaults+=($line); done < "$conf"
      dsrc="$conf"
    fi
    if [[ -n "${HPC_PICK_DEFAULTS:-}" ]]; then
      # shellcheck disable=SC2206  # intentionally word-split
      defaults+=(${HPC_PICK_DEFAULTS}); dsrc="${dsrc:+$dsrc + }HPC_PICK_DEFAULTS"
    fi
    if [[ ${#defaults[@]} -gt 0 ]]; then
      echo "pick defaults from $dsrc: ${defaults[*]}"
      set -- ${defaults[@]+"${defaults[@]}"} "$@"
    fi
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --gpus)        gpus="$2"; shift 2 ;;
        --vram)        vram="$2"; shift 2 ;;
        --gpu-model)   model=$(printf '%s' "$2" | tr 'A-Z' 'a-z'); shift 2 ;;
        --family)      family=$(printf '%s' "$2" | tr 'A-Z' 'a-z'); shift 2 ;;
        --cpus)        cpus="$2"; shift 2 ;;
        --mem)         mem="$2"; shift 2 ;;
        --cpu-only)    cpu_only=1; shift ;;
        --partition)   parts="$2"; shift 2 ;;
        --avoid)       avoid="$2"; shift 2 ;;
        --prefer)      prefer="$2"; shift 2 ;;
        --top)         limit="$2"; shift 2 ;;
        --days)        bdays="$2"; shift 2 ;;
        --no-busyness) busy=0; shift ;;
        -h|--help)
          cat <<'EOF'
hpc pick — recommend a partition + #SBATCH header for a job, from what your account may use.

  --gpus N           number of GPUs per node (default 1; implies a GPU class)
  --vram GB           minimum VRAM per GPU card (default 48 = current-generation cards; 0 = anything)
  --gpu-model TAG     pin a model: l40 l40s a40 a100 rtx6000pro h200nvl rtx-6000 t4 rtx-2080ti
  --family NAME       pin a family: turing ampere lovelace blackwell hopper
  --cpus N            hardware threads per node (default 1)
  --mem GB            memory per node (default 1)
  --cpu-only          CPU classes only (keeps GPU nodes free)
  --partition a,b     only consider these partitions (default: all you may use)
  --avoid a,b         drop node classes that live only in these partitions (e.g. --avoid itc-gpu)
  --prefer a,b        rank classes reachable through these partitions first
  --top N             show N candidates (default 5)
  --days N            busyness window in days (default 7);  --no-busyness skips the accounting scan

Personal defaults: lines of ~/.config/hpc/pick.conf and $HPC_PICK_DEFAULTS are parsed first, so any flag
given here overrides them (boolean flags such as --cpu-only cannot be un-set, only avoided). The defaults are
meant for habits (--avoid itc-gpu, --prefer main-gpu), not for facts: facts come from the live data.

Ranking: idle nodes right now, then 7-day occupancy (least busy first), then --prefer, then fleet size,
then tightest fit. The busyness scan reads ~90k accounting rows (≈3 s on the head node).
EOF
          exit 0 ;;
        *) die "unknown pick option: $1 (see: hpc pick --help)" ;;
      esac
    done
    [[ -n "$gpus" && $cpu_only -eq 1 ]] && die "--gpus and --cpu-only are mutually exclusive"
    # Contention from accounting (7-day occupancy per class, best window, queue wait per partition).
    occ="" win="" wait=""
    py="$(cd "$(dirname "$0")" && pwd)/busyness.py"
    if [[ $DRY_RUN -eq 0 && $busy -eq 1 && -f "$py" ]] && command -v python3 >/dev/null; then
      if j=$(ssh "${SSH_OPTS[@]}" "$HPC_HOST" "python3 - --json --days $bdays" < "$py" 2>/dev/null) && [[ -n "$j" ]]; then
        eval "$(printf '%s' "$j" | python3 -c '
import json, shlex, sys
d = json.load(sys.stdin)
occ = "|".join("%s/%s/%s:%s" % (r["class"], r["gpus_per_node"], r["ram_gb"], r["occupancy_pct"]) for r in d["classes"])
win = "|".join("%s/%s/%s:%s" % (r["class"], r["gpus_per_node"], r["ram_gb"], r["least_busy_windows"][0]) for r in d["classes"] if r["least_busy_windows"])
wait = "|".join("%s:%s" % (q["partition"], q["median_wait"]) for q in d["queue_wait"])
print("occ=%s; win=%s; wait=%s" % (shlex.quote(occ), shlex.quote(win), shlex.quote(wait)))' 2>/dev/null)" || true
      fi
    fi
    remote_bash "$NODE_CLASSES"'
gpus=$1 vram=$2 model=$3 family=$4 cpus=$5 mem=$6 cpu_only=$7 parts=$8 limit=$9 avoid=${10} prefer=${11} occ=${12} win=${13} wait=${14} bdays=${15}
declare -A OCC WIN WAIT
IFS="|" read -ra kv <<< "$occ";  for e in "${kv[@]}"; do [[ "$e" == *:* ]] && OCC[${e%:*}]=${e##*:}; done
IFS="|" read -ra kv <<< "$win";  for e in "${kv[@]}"; do [[ "$e" == *:* ]] && WIN[${e%:*}]=${e##*:}; done
IFS="|" read -ra kv <<< "$wait"; for e in "${kv[@]}"; do [[ "$e" == *:* ]] && WAIT[${e%:*}]=${e##*:}; done
[[ $gpus == "" ]] && { [[ $cpu_only -eq 1 ]] && gpus=0 || gpus=1; }
[[ $vram == "" ]] && vram=48      # unspecified = current-generation cards; --vram 0 for anything
[[ $cpu_only -eq 1 ]] && gpus=0
IFS="," read -ra want <<< "$parts"
echo "requirements: $([[ $gpus -gt 0 ]] && echo "$gpus GPU(s) ≥ ${vram} GB${model:+ model=$model}${family:+ family=$family}" || echo "CPU-only") | ${cpus} threads | ${mem} GB RAM"
echo "-- candidates (ranked by idle now, then ${bdays}-day occupancy, then fleet size):"
emit() {   # one tab-separated record per feasible node class, sort key first
  for key in "${!CNT[@]}"; do
    IFS="|" read -r c m g f <<<"$key"; parse_gres "$g"; mdl=$(model_of "$f"); v=${VRAM[$mdl]:-0}
    allparts=$(tr "," "\n" <<<"${PARTS[$key]}" | grep -v "^$" | sort -u)
    if [[ ${#want[@]} -gt 0 && -n "${want[0]}" ]]; then
      keep=0; for w in "${want[@]}"; do grep -qx "$w" <<<"$allparts" && keep=1; done
      [[ $keep -eq 1 ]] || continue
    fi
    [[ $c -ge $cpus ]] || continue
    [[ $(( m / 1024 )) -ge $mem ]] || continue
    if [[ $gpus -gt 0 ]]; then
      [[ $GN -ge $gpus ]] || continue
      [[ $vram -gt 0 && $v -lt $vram ]] && continue
      [[ -n "$model" && $mdl != "$model" ]] && continue
      [[ -n "$family" && $GF != "$family" ]] && continue
    else
      [[ $GF != - ]] && continue
    fi
    pref=$(grep -vx main <<<"$allparts" | head -1); [[ -n "$pref" ]] || pref=$(head -1 <<<"$allparts")
    if [[ -n "$avoid" ]]; then left=$(grep -vxFf <(tr "," "\n" <<<"$avoid") <<<"$allparts"); [[ -n "$left" ]] || continue; fi
    boost=0; [[ -n "$prefer" ]] && grep -qxFf <(tr "," "\n" <<<"$prefer") <<<"$allparts" && boost=1
    idle=${IDLE[$key]:-0}; total=${CNT[$key]}; ck="$mdl/$GN/$(( m / 1024 ))"
    oc=${OCC[$ck]:-999}; wc=${WIN[$ck]:--}; wt=${WAIT[$pref]:--}
    printf "%05d %03d %1d %04d %07d %04d|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n" $(( 99999 - idle )) "$oc" $(( 1 - boost )) $(( 9999 - total )) "$m" "$v" \
      "$mdl" "$v" "$GN" "$GF" "$c" "$(( m / 1024 ))" "$idle/$total" "$oc" "$wc" "$wt" "$pref" "$(paste -sd, - <<<"$allparts")"
  done
}
rows=(); mapfile -t rows < <(emit | sort -t"|" -k1,1 | head -n "$limit")
n=0; best=""
for row in "${rows[@]}"; do
  IFS="|" read -r _ mdl v gn gf c ram idle oc wc wt pref allp <<<"$row"
  n=$((n + 1)); [[ $n -eq 1 ]] && best="$mdl|$v|$gn|$gf|$c|$ram|$pref|$allp|$oc|$wc|$wt"
  [[ $mdl == - ]] && mdl="(cpu)"; [[ $gf == - ]] && gf="-"; [[ $oc == 999 ]] && oc="?"
  if [[ "$wc" != - ]]; then wc="least busy: $wc"; else wc=""; fi
  if [[ "$wt" != - ]]; then wt="median wait on $pref: $wt"; else wt=""; fi
  if [[ $mdl == "(cpu)" ]]; then
    printf "   %d. %-12s %3s thr %5s GB   idle %s   busy ${bdays}d %3s%%   %-22s %-26s partitions: %s\n" "$n" "$mdl" "$c" "$ram" "$idle" "$oc" "$wc" "$wt" "$allp"
  else
    printf "   %d. %-12s %3s GB/GPU  %s GPU/node  %-8s %3s thr %5s GB   idle %s   busy ${bdays}d %3s%%   %-22s %-26s partitions: %s\n" "$n" "$mdl" "$v" "$gn" "$gf" "$c" "$ram" "$idle" "$oc" "$wc" "$wt" "$allp"
  fi
done
if [[ -z "$best" ]]; then
  echo "   (none of the node classes you may use satisfies this; see: hpc machines / hpc busyness)"
  exit 1
fi
IFS="|" read -r mdl v gn gf c ram pref allp oc wc wt <<<"$best"
[[ $mdl == - ]] && c=$cpus ram=$mem
[[ $oc == 999 ]] && oc="?"
[[ "$wc" == - ]] && wc=""; [[ "$wt" == - ]] && wt=""
echo "-- recommended #SBATCH header  (partition=$pref; also in: ${allp//,/ }; ${bdays}-day occupancy ${oc}%${wc:+; $wc}${wt:+; median queue wait on $pref: $wt})"
echo "#SBATCH --partition=$pref"
if [[ $mdl != - ]]; then
  echo "#SBATCH --gres=gpu:$gf:$([[ $gpus -gt 0 ]] && echo "$gpus" || echo 1)"
  if [[ -n "$model" ]]; then echo "#SBATCH --constraint=$mdl"; else echo "#SBATCH --constraint=$mdl      # optional: pin this model; delete to accept any $gf GPU"; fi
  [[ $mdl =~ ^(a100|a40|rtx-6000)$ && $gpus -gt 1 ]] && echo "#SBATCH --sockets-per-node=1     # NVLink pair"
fi
echo "#SBATCH --cpus-per-task=$( [[ $mdl == - ]] && echo "$cpus" || echo $(( c < 8 ? c : 8 )) )"
echo "#SBATCH --mem=$( [[ $mdl == - ]] && echo "${mem}G" || echo $(( ram / 4 < 1 ? ram : ram / 4 ))G )"
echo "#SBATCH --time=04:00:00          # shorter limits backfill into gaps sooner"
echo "#SBATCH --job-name=JOB"
echo "#SBATCH --output=logs/%x-%j.out"
echo "note: threads/memory above are sensible defaults for this class; raise them if the job needs it (nodes are exclusive anyway)."
when=$(grep -oE "^[A-Z][a-z]{2} [0-9]{2}-[0-9]{2}" <<<"${wc:-}" | head -1)
if [[ -n "$when" ]]; then echo "timing: this class is least busy around $when; submit long jobs before the weekend."; fi
' "$gpus" "$vram" "$model" "$family" "$cpus" "$mem" "$cpu_only" "$parts" "$limit" "$avoid" "$prefer" "$occ" "$win" "$wait" "$bdays" ;;

  busyness)
    days=7 bargs=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --days) days="$2"; bargs+=(--days "$2"); shift 2 ;;
        --json|--all) bargs+=("$1"); shift ;;
        *) die "busyness [--days N] [--json] [--all]" ;;
      esac
    done
    py="$(cd "$(dirname "$0")" && pwd)/busyness.py"
    [[ -f "$py" ]] || die "missing $py"
    if [[ $DRY_RUN -eq 1 ]]; then echo "[dry-run] ssh $HPC_HOST python3 - ${bargs[*]:-} < $py"; exit 0; fi
    ssh "${SSH_OPTS[@]}" "$HPC_HOST" "python3 - ${bargs[*]:-}" < "$py" ;;

  modules)
    remote_bash 'root=/software/Modulefiles
if [[ $# -eq 0 ]]; then
  for g in $(ls "$root" 2>/dev/null); do printf "   %-14s %s\n" "$g" "$(ls "$root/$g" 2>/dev/null | paste -sd" " - | cut -c1-100)"; done
  echo "(truncated; run: hpc modules <group>)"
else
  for g in "$@"; do
    [[ -d "$root/$g" ]] || { echo "no module group named \"$g\"; run: hpc modules"; exit 1; }
    printf "   %s = %s\n" "$g" "$(ls "$root/$g" | paste -sd" " -)"
  done
fi
echo "hint: hpc run \"module load <group>/<version> && <cmd>\"   (never run module avail/show over ssh: they hang)"' "$@" ;;

  tty)
    [[ $# -ge 1 ]] || die "tty '<cmd>'  (needs a command; meant for sinteractive/top-node in a real terminal)"
    q=$(printf '%q' "$*")
    if [[ $DRY_RUN -eq 1 ]]; then echo "[dry-run] ssh -tt $HPC_HOST bash -lc $q"; exit 0; fi
    exec ssh -tt "${SSH_OPTS[@]}" "$HPC_HOST" "bash -lc $q" ;;

  *) die "unknown command: $CMD (see --help)" ;;
esac
